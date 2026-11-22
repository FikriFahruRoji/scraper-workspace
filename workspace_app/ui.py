"""Shared look and feel: page header cards, badges, number formatting and CSS.

The layout follows a studio-style workspace: a light page, white rounded cards
with thin grey borders, a purple accent, option cards for choices and a
catalogue of sources that leads to one form per source.
"""
import base64
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re

import streamlit as st

# Chart colors for plotly, readable on both surfaces (Streamlit's own charts follow the theme).
CHART_ACCENT = "#2B93AE"
CHART_LINE = "rgba(130, 146, 162, .45)"
CHART_SURFACE = "rgba(130, 146, 162, .8)"

# Navigation targets registered by the entrypoint, so views can link to pages
# without importing the entrypoint. Empty when a view runs on its own (tests).
PAGES = {}

STATUS = {
    "running": "Berjalan", "stopping": "Sedang menghentikan", "completed": "Selesai",
    "failed": "Gagal", "partial": "Selesai sebagian", "stopped": "Dihentikan",
    "interrupted": "Terputus",
}
STATUS_COLORS = {
    "running": "blue", "stopping": "orange", "completed": "green", "failed": "red",
    "partial": "orange", "stopped": "gray", "interrupted": "gray",
}
STATUS_ICONS = {
    "running": ":material/progress_activity:", "stopping": ":material/pause_circle:",
    "completed": ":material/check_circle:", "failed": ":material/error:",
    "partial": ":material/incomplete_circle:", "stopped": ":material/stop_circle:",
    "interrupted": ":material/link_off:",
}

# What a status means for the data, shown next to the status in the dataset view.
STATUS_HELP = {
    "partial": "Pengambilan berhenti sebelum tuntas, misalnya karena batas waktu habis atau sumber "
               "berhenti mengirim data. Semua data yang sempat terkumpul tetap disimpan.",
    "stopped": "Kamu menghentikan pekerjaan ini. Data yang sudah terkumpul tetap disimpan.",
    "interrupted": "Aplikasi tertutup saat pekerjaan berjalan. Data sampai penyimpanan terakhir tetap disimpan.",
    "failed": "Pengambilan tidak berhasil.",
}

# Scraper error codes that reach the user as the job message.
FRIENDLY_ERRORS = {
    "no_posts": "Tidak ada post yang ditemukan untuk input ini.",
    "no_places": "Tidak ada tempat yang ditemukan untuk kata kunci ini.",
    "no_comments": "Tidak ada komentar yang ditemukan untuk post ini.",
    "login_required": "Platform meminta login. Klik Login platform lalu coba lagi.",
    "blocked": "Permintaan diblokir oleh platform. Coba lagi nanti.",
    "browser_error": "Browser gagal dijalankan. Periksa koneksi lalu coba lagi.",
    "parse_error": "Format respons platform berubah sehingga data tidak bisa dibaca.",
    "timeout": "Batas waktu habis sebelum data selesai diambil.",
    "gangguan": "Koneksi ke platform terganggu.",
    "waktu": "Batas waktu habis sebelum data selesai diambil.",
    "stagnan": "Tidak ada data baru dalam beberapa percobaan, pengambilan dihentikan.",
}

LIGHT_TOKENS = """--bg: #EEF1F4; --surface: #F8FAFB; --raised: #FFFFFF; --ink: #101820; --muted: #4B5663;
  --line: #D5DCE4; --accent: #0E7490; --accent-ink: #F4FBFD; --accent-soft: rgba(14, 116, 144, .09);
  --danger: #B3261E; --danger-soft: rgba(179, 38, 30, .08); --danger-line: rgba(179, 38, 30, .35);
  --warn: #8F4A0B; --shadow: 0 10px 24px -14px rgba(16, 24, 32, .28); --sidebar: #F8FAFB;"""
DARK_TOKENS = """--bg: #0B1117; --surface: #121A22; --raised: #18232D; --ink: #E4EAF0; --muted: #9AA8B6;
  --line: #243240; --accent: #4DC0D8; --accent-ink: #04212A; --accent-soft: rgba(77, 192, 216, .12);
  --danger: #F07178; --danger-soft: rgba(240, 113, 120, .10); --danger-line: rgba(240, 113, 120, .4);
  --warn: #E8A24A; --shadow: 0 10px 24px -14px rgba(0, 0, 0, .7); --sidebar: #0E151C;"""


def theme_type():
    """The theme Streamlit is showing now ("light" or "dark"), or None before the browser reported it."""
    try:
        return st.context.theme.type
    except Exception:
        return None


def token_css(theme):
    """Color tokens. Python sets the starting theme (the system setting until the browser reports one);
    THEME_SCRIPT then marks the theme Streamlit is really showing, so the in-app chooser wins."""
    start = (f":root {{ {DARK_TOKENS} }}" if theme == "dark" else f":root {{ {LIGHT_TOKENS} }}"
             if theme == "light" else
             f":root {{ {LIGHT_TOKENS} }} @media (prefers-color-scheme: dark) {{ :root {{ {DARK_TOKENS} }} }}")
    return (start + f' :root[data-nct-theme="light"] {{ {LIGHT_TOKENS} }}'
            f' :root[data-nct-theme="dark"] {{ {DARK_TOKENS} }}')


# Streamlit does not rerun the script when the theme is changed in its menu, so the page reads its own
# background colour and marks the active theme. Static code, no user content.
THEME_SCRIPT = """
(function () {
  if (window.__nctTheme) return;
  window.__nctTheme = true;
  function apply() {
    var app = document.querySelector('.stApp');
    var rgb = app && getComputedStyle(app).backgroundColor.match(/\\d+/g);
    if (!rgb) return;
    var light = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
    document.documentElement.setAttribute('data-nct-theme', light < 0.5 ? 'dark' : 'light');
  }
  apply();
  setInterval(apply, 300);
})();
"""


# Tokens are injected separately (see inject_css) so they follow the theme chosen in the app menu.
CSS = """
<style>
/* Page */
[data-testid="stMainBlockContainer"] {
    width: 100%;
    max-width: none;
    padding-top: 2.75rem;
    padding-inline: clamp(1rem, 2vw, 2rem);
}
[data-testid="stSidebar"] { border-right: 1px solid var(--line); }
[data-testid="stSidebarNavSeparator"] { display: none; }
[data-testid="stSidebarNav"] a[aria-current="page"] { background: var(--accent); }
[data-testid="stSidebarNav"] a[aria-current="page"] span,
[data-testid="stSidebarNav"] a[aria-current="page"] [data-testid="stIconMaterial"] { color: var(--accent-ink) !important; }
[data-testid="stNavSectionHeader"] { text-transform: uppercase; letter-spacing: .06em;
    font-size: .8rem; color: var(--muted); font-weight: 500; }

/* Wordmark: text only, so it follows the theme tokens. The logo image is a blank placeholder. */
[data-testid="stSidebarHeader"] img { display: none !important; }
/* The home page wraps the logo in a plain div, every other page in a link button: match both. */
[data-testid="stSidebarHeader"] > :has(> img[data-testid="stSidebarLogo"])::after { content: "nOCturnal Scraper";
    color: var(--ink); font-weight: 700; font-size: 1.05rem; letter-spacing: -.01em; white-space: nowrap; }

/* Sidebar: settings and credit sit at the bottom and stay visible while the menu scrolls.
   --sidebar matches [theme.*.sidebar] backgroundColor in .streamlit/config.toml. */
[data-testid="stSidebarContent"] { display: flex; flex-direction: column; min-height: 100%; }
/* Sticky on the user-content box, not the block inside it: a sticky element only moves within its
   parent, and the block's own wrappers are exactly its height. This box holds only the block. */
[data-testid="stSidebarUserContent"] { flex: 1; display: flex; flex-direction: column;
    position: sticky; bottom: 0; z-index: 2; background: var(--sidebar); padding: 0; }
[data-testid="stSidebarUserContent"] div:has(.st-key-sidebar-bottom) { flex: 1; display: flex; flex-direction: column; }
.st-key-sidebar-bottom { margin-top: auto; justify-content: flex-end; padding-bottom: 1rem; }
.st-key-sidebar-footer { border-top: 1px solid var(--line); padding-top: .75rem; margin-top: .5rem; }
.st-key-sidebar-footer [data-testid="stCaptionContainer"] { font-size: .8rem; }
.settings-title { text-transform: uppercase; letter-spacing: .06em; font-size: .8rem; font-weight: 500; color: var(--muted);
    border-top: 1px solid var(--line); padding-top: .75rem; }

/* Header card */
.st-key-page-header { background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
    padding: 18px 20px; margin-bottom: .25rem; }
.st-key-page-header h1 { font-size: 1.4rem !important; padding: 0 !important; margin: 0 !important; }
.st-key-page-header [data-testid="stCaptionContainer"] { margin-top: -2px; }
.st-key-page-header [data-testid="stHeadingWithActionElements"] a { display: none; }

/* Icon tiles */
[class*="st-key-tile"] { background: var(--accent-soft); color: var(--accent);
    border-radius: 12px; width: 44px !important; height: 44px; min-width: 44px;
    justify-content: center !important; align-items: center !important; flex: 0 0 44px !important; gap: 0 !important; }
[class*="st-key-tile"] > div { width: auto !important; height: auto !important; }
[class*="st-key-tile"] p { margin: 0 !important; line-height: 1 !important; }
[class*="st-key-tile"] span[role="img"] { font-size: 1.45rem !important; color: var(--accent);
    vertical-align: middle !important; }

/* Source catalogue rows */
[class*="st-key-source-row-"] { background: var(--surface); border: 1px solid var(--line);
    border-radius: 16px; padding: 16px 20px; row-gap: .75rem; }
.source-description { display: grid; gap: .3rem; }
.source-description h2 { margin: 0; padding: 0; font-size: 1.05rem; line-height: 1.3; }
.source-description p { margin: 0; color: var(--muted); font-size: .875rem; line-height: 1.5; }
[class*="st-key-source-row-"] .stButton button { white-space: nowrap; }
/* Catalogue grid: never more than three columns, fewer when a column would be narrower than 340px, and never
   wider than its container (a 375px phone has about 345px). */
.st-key-source-grid { display: grid !important; gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, max(340px, calc((100% - 2rem) / 3))), 1fr)); }
.st-key-source-grid > div { display: flex; min-width: 0; }
.st-key-source-grid [class*="st-key-source-row-"] { flex: 1; min-width: 0; }
.source-chip { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 44px;
    width: 44px; height: 44px; border-radius: 12px; border: 1px solid var(--line); }
.source-chip img { width: 24px; height: 24px; display: block; }
.source-chip .glyph { width: 24px; height: 24px; display: block; background: currentColor;
    -webkit-mask: var(--glyph) center / contain no-repeat; mask: var(--glyph) center / contain no-repeat; }
.source-chip.brand { background: #F4F6F8; }  /* logos need a light ground in both themes */
.source-chip.symbol { background: var(--accent-soft); color: var(--accent); border-color: transparent; }

/* Plain panels */
[class*="st-key-panel"] { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px; }

@keyframes workspace-loading-spin {
    to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: no-preference) {
    span[role="img"][aria-label="progress_activity icon"] {
        animation: workspace-loading-spin 1s linear infinite;
    }
}
@media (prefers-reduced-motion: reduce) {
    :is(.st-key-panel-activity, .st-key-activity-inline) [data-testid="stAlertDynamicIcon"] {
        animation: none !important;
    }
}

/* Summary panel (recap before starting a job) */
[class*="st-key-summary"] { background: var(--bg); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
[class*="st-key-summary"] [data-testid="stMarkdownContainer"] p { margin: 0; font-size: .9rem; }
[class*="st-key-summary"] hr { margin: .35rem 0; border-color: var(--line); }
.missing { color: var(--warn); font-weight: 500; }

/* Option cards: radios with captions */
[data-testid="stRadio"] div[role="radiogroup"] { display: grid !important; gap: 10px !important;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
[data-testid="stRadio"] div[role="radiogroup"] > div { border: 1px solid var(--line); border-radius: 12px; padding: 10px 14px;
    background: var(--raised); margin: 0 !important; transition: border-color .15s, background .15s; }
[data-testid="stRadio"] div[role="radiogroup"] > div:hover { border-color: var(--accent); }
[data-testid="stRadio"] div[role="radiogroup"] > div:has(label[data-selected="true"]) { border-color: var(--accent); background: var(--accent-soft); }
[data-testid="stRadio"] div[role="radiogroup"] > div:has(label[data-selected="true"]) label p { color: var(--accent); font-weight: 500; }

/* Job list rows */
[class*="st-key-jobrow-"] { border-top: 1px solid var(--line); padding: 12px 4px; }
[class*="st-key-joblist"] { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 4px 18px 10px; }
[class*="st-key-jobrow-"] [data-testid="stMarkdownContainer"] p { margin-bottom: .1rem; }
.job-head { font-size: .8rem; letter-spacing: .05em; text-transform: uppercase; color: var(--muted); }
@media (max-width: 640px) { [class*="st-key-jobhead"] { display: none; } }
[class*="st-key-delete-"] button { color: var(--danger); border-color: var(--danger-line); }
[class*="st-key-delete-"] button:hover { background: var(--danger-soft); border-color: var(--danger); color: var(--danger); }

/* Method record: how a dataset was collected */
dl.method { display: grid; grid-template-columns: minmax(130px, 34%) 1fr; margin: 0; font-size: .9rem;
    border-top: 1px solid var(--line); }
dl.method dt, dl.method dd { margin: 0; padding: 8px 0; border-bottom: 1px solid var(--line); }
dl.method dt { color: var(--muted); }
dl.method dd { color: var(--ink); font-weight: 500; overflow-wrap: anywhere; }

/* Browser surfaces: selection, focus, numerals */
::selection { background: var(--accent-soft); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
[data-testid="stMetricValue"], [data-testid="stDataFrame"], dl.method dd { font-variant-numeric: tabular-nums; }
[data-testid="stMetricValue"] { font-size: 1.7rem; }

/* Buttons */
.stButton button[kind="primary"], .stDownloadButton button[kind="primary"] { font-weight: 500; color: var(--accent-ink); }
.stButton button[kind="primary"] p, .stDownloadButton button[kind="primary"] p { color: var(--accent-ink); }
.stButton button[kind="primary"]:disabled, .stButton button[kind="primary"]:disabled p { color: var(--muted); }
</style>
"""


THEME_LABELS = {"system": "System", "light": "Light", "dark": "Dark"}


def apply_theme_pref(pref, paths):
    """Make Streamlit show the chosen theme on every page.

    Streamlit keeps its theme choice per page address in localStorage and reads it on load, so the choice is
    written for all pages and the page reloads once when the current page held a different value.
    """
    label = json.dumps(json.dumps(THEME_LABELS.get(pref, "System")))
    script = """
(function () {
  var value = %s, paths = %s, here = location.pathname, stale = false;
  paths.forEach(function (path) {
    var key = 'stActiveTheme-' + path + '-v2';
    if (localStorage.getItem(key) !== value) {
      localStorage.setItem(key, value);
      if (path === here) stale = true;
    }
  });
  var seen = 'nct-theme-reload';
  if (stale && sessionStorage.getItem(seen) !== value + here) {
    sessionStorage.setItem(seen, value + here);
    location.reload();
  } else if (!stale) {
    sessionStorage.removeItem(seen);
  }
})();
""" % (label, json.dumps(list(paths)))
    st.html(f"<script>{script}</script>", unsafe_allow_javascript=True)


def inject_css():
    st.html(CSS.replace("</style>", token_css(theme_type()) + "\n</style>", 1))
    st.html(f"<script>{THEME_SCRIPT}</script>", unsafe_allow_javascript=True)


ICONS = Path(__file__).resolve().parent / "resources" / "icons"
# Logos of the services a source reads from, in each brand's own color. Black logos use the page ink so they
# stay readable. Sources without a logo of their own get a themed symbol instead.
BRAND_COLORS = {"instagram": "#E4405F", "youtube": "#FF0000", "x": "#101820", "threads": "#101820",
                "gmaps": "#4285F4", "gmaps_places": "#4285F4", "playstore": "#01875F",
                "google_scholar": "#4285F4"}
SVG_RE = re.compile(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*)</svg>', re.S)


@lru_cache(maxsize=None)
def _icon_uri(source):
    """A data: URI of a source icon read from resources/icons; empty when the file is missing.

    st.html strips inline <svg>, but allows images and CSS values that carry a data: URI.
    """
    try:
        match = SVG_RE.search((ICONS / f"{source}.svg").read_text(encoding="utf-8"))
    except OSError:
        return ""
    if not match:
        return ""
    box, inner = match.groups()
    inner = re.sub(r"<title>.*?</title>", "", inner, flags=re.S)
    fill = BRAND_COLORS.get(source, "#000000")  # symbols are used as a mask, so only their shape matters
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" fill="{fill}">{inner}</svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def source_chip(source):
    """HTML for the rounded icon that identifies a data source: its logo, or a symbol in the theme accent."""
    uri = _icon_uri("gmaps" if source == "gmaps_places" else source)
    if not uri:
        return ""
    if source in BRAND_COLORS:
        return f'<span class="source-chip brand"><img src="{uri}" alt="" width="24" height="24"></span>'
    return f'<span class="source-chip symbol"><span class="glyph" style="--glyph:url({uri})"></span></span>'


def fmt(number):
    """Integer with Indonesian thousands separators: 102.424."""
    return f"{int(number):,}".replace(",", ".")


def page_header(title, subtitle="", *, back=None, actions=None, split=(2, 1)):
    """Header card: optional back button, title, subtitle, right-side actions.

    `actions` is a callable that renders into the right-hand column.
    """
    with st.container(key="page-header"):
        cols = st.columns(list(split) if actions else [1], vertical_alignment="center")
        with cols[0]:
            with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
                if back and back in PAGES:
                    if st.button("Kembali", icon=":material/arrow_back:", key="header-back"):
                        st.switch_page(PAGES[back])
                with st.container():
                    st.title(title, anchor=False)
                    if subtitle:
                        st.caption(subtitle)
        if actions:
            with cols[1]:
                actions()


def status_markdown(status):
    """Status pill as inline markdown, for places where it shares a line with text."""
    return f":{STATUS_COLORS.get(status, 'gray')}-badge[{STATUS_ICONS.get(status, '')} {STATUS.get(status, status)}]"


def status_badge(status):
    st.badge(STATUS.get(status, status), icon=STATUS_ICONS.get(status),
             color=STATUS_COLORS.get(status, "gray"))


def friendly(message):
    if not message:
        return message
    parts = [FRIENDLY_ERRORS.get(part.strip(), part.strip()) for part in str(message).split(";")]
    return "; ".join(part for part in parts if part)


def parse_time(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
          "September", "Oktober", "November", "Desember"]


def long_date(value):
    """'5 September 2026 pukul 23.04' in local time."""
    moment = parse_time(value)
    if not moment:
        return "-"
    moment = moment.astimezone()
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year} pukul {moment:%H.%M}"


def short_datetime(value):
    """'19 Nov 2026 09.58' in local time, for tables and lists."""
    moment = parse_time(value)
    if not moment:
        return "-"
    moment = moment.astimezone()
    return f"{short_date(moment)} {moment:%H.%M}"


def short_date(value):
    """'21 Okt 2026': the one compact date used next to long ones."""
    return f"{value.day} {MONTHS[value.month - 1][:3]} {value.year}"


def duration(start, end):
    first, last = parse_time(start), parse_time(end)
    if not first or not last:
        return None
    seconds = max(0, int((last - first).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [f"{hours} jam"] if hours else []
    if minutes:
        parts.append(f"{minutes} menit")
    if seconds or not parts:
        parts.append(f"{seconds} detik")
    return " ".join(parts)


def ago(value):
    moment = parse_time(value)
    if not moment:
        return "belum ada"
    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    for size, unit in ((86400, "hari"), (3600, "jam"), (60, "menit")):
        if seconds >= size:
            return f"{seconds // size} {unit} lalu"
    return "baru saja"


def highlight_nav(path="scraping"):
    """Keep a menu item highlighted on source forms, which are hidden from the menu."""
    st.html(f"""<style>[data-testid="stSidebarNav"] a[href$="/{path}"] {{ background: var(--accent) !important; }}
    [data-testid="stSidebarNav"] a[href$="/{path}"] span {{ color: var(--accent-ink) !important; }}</style>""")
