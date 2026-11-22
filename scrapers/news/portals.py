"""Penemuan dan parsing artikel dengan AsyncStealthySession Scrapling."""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote

from .core import (
    Portal,
    clean_paragraph,
    clean_ws,
    parse_date_text,
    parse_iso,
    strip_title_suffix,
)

# ---------------------------------------------------------------------------
# Daftar portal
# ---------------------------------------------------------------------------
PORTALS: dict[str, Portal] = {
    "kompas": Portal(
        key="kompas", label="Kompas.com", base="https://search.kompas.com", kind="search",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*kompas\.com/read/(\d{4})/(\d{2})/(\d{2})/"),
        body_selectors=["div.read__content p"],
        dateline=re.compile(r"^[A-Z][A-Z .,]*\s*,\s*KOMPAS\.com\s*[-–—]\s*"),
        title_suffix=re.compile(r"\s*[|\-–]\s*Kompas\.com\s*$", re.I),
    ),
    "detik": Portal(
        key="detik", label="Detik.com", base="https://www.detik.com", kind="search",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*detik\.com/.+/d-\d+"),
        body_selectors=["div.detail__body-text p", "article p"],
        dateline=re.compile(r"^[A-Z][A-Za-z .]{2,25}\s*[-–—]\s*"),
        title_suffix=re.compile(r"\s*[|\-–]\s*detik\w*\s*$", re.I),
    ),
    "cnn": Portal(
        key="cnn", label="CNN Indonesia", base="https://www.cnnindonesia.com", kind="tag",
        article_re=re.compile(r"^https?://www\.cnnindonesia\.com/[a-z\-]+/(\d{14})-"),
        body_selectors=["div.detail-text p", "article p"],
        dateline=re.compile(r"^[A-Z][A-Za-z .,]*\s*,\s*CNN Indonesia\s*[-–—]+\s*", re.I),
        title_suffix=re.compile(r"\s*[|\-–]\s*CNN Indonesia\s*$", re.I),
        tag_page="?page={n}",
        light=True,
    ),
    "cnbc": Portal(
        key="cnbc", label="CNBC Indonesia", base="https://www.cnbcindonesia.com", kind="tag",
        article_re=re.compile(r"^https?://www\.cnbcindonesia\.com/[a-z\-]+/(\d{14})-"),
        body_selectors=["div.detail-text p", "article p"],
        dateline=re.compile(r"^[A-Z][A-Za-z .,]*\s*,\s*CNBC Indonesia\s*[-–—]+\s*", re.I),
        title_suffix=re.compile(r"\s*[|\-–]\s*CNBC Indonesia\s*$", re.I),
        tag_page="?page={n}",
    ),
    "republika": Portal(
        key="republika", label="Republika", base="https://www.republika.co.id", kind="tag",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*republika\.co\.id/berita/[a-z0-9]+/"),
        body_selectors=["div.article-content p", "article p"],
        title_suffix=re.compile(r"\s*[|\-–]\s*Republika Online\s*$", re.I),
        list_selector="ul.list-group li.list-group-item a",
        tag_page="/{n}",
    ),
    "suara": Portal(
        key="suara", label="Suara.com", base="https://www.suara.com", kind="tag",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*suara\.com/(?:[a-z\-]+/)?(20\d\d)/(\d\d)/(\d\d)/\d+/"),
        body_selectors=["div.article-content p", "article p"],
        dateline=re.compile(r"^Suara[\w.]*\.id\s*[-–—]+\s*"),
        title_suffix=re.compile(r"\s*[|\-–]\s*Suara\.com\s*$", re.I),
        list_selector="div.article-kanal-list a",
        tag_page="?page={n}",
    ),
    "tribun": Portal(
        key="tribun", label="Tribunnews", base="https://www.tribunnews.com", kind="tag",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*tribunnews\.com/([a-z\-]+)/(\d{6,})/"),
        body_selectors=["div.txt-article p", "article p"],
        dateline=re.compile(r"^TRIBUNNEWS\.COM\s*[-–—]+\s*"),
        title_suffix=re.compile(r"\s*[|\-–]\s*Tribunnews\.com\s*$", re.I),
        list_selector="ul.lsi a",
        tag_page="?page={n}",
    ),
    "tempo": Portal(
        key="tempo", label="Tempo.co", base="https://www.tempo.co", kind="rss",
        article_re=re.compile(r"^https?://[a-z0-9.\-]*tempo\.co/read/\d+/"),
        body_selectors=["div.detail-body p", "article p", "div[class*=content] p"],
        title_suffix=re.compile(r"\s*[|\-–]\s*Tempo\.co\s*$", re.I),
        feeds=["https://rss.tempo.co/"],  # subdomain /bisnis & /nasional mengembalikan body kosong
    ),
    "idxchannel": Portal(
        key="idxchannel", label="IDX Channel", base="https://www.idxchannel.com", kind="tag",
        article_re=re.compile(r"^https?://www\.idxchannel\.com/[a-z\-]+/[a-z0-9\-]{20,}$"),
        body_selectors=["div[class*=content] p", "article p"],
        title_suffix=re.compile(r"\s*[|\-–]\s*IDX ?Channel\s*$", re.I),
        list_selector="div.container-news div.bt-con a",
        tag_page=None,
    ),
    "jpnn": Portal(
        key="jpnn", label="JPNN", base="https://www.jpnn.com", kind="tag",
        article_re=re.compile(r"^https?://www\.jpnn\.com/[a-z\-]+/[a-z0-9\-]{20,}$"),
        body_selectors=["div[class*=content] p", "article p"],
        dateline=re.compile(r"^jpnn\.com\s*,\s*[A-Za-z .]+\s*[-–—]+\s*"),
        title_suffix=re.compile(r"\s*[|\-–]\s*JPNN\.com\s*$", re.I),
        list_selector="ul.content-list article a",
        tag_page="?page={n}",
    ),
}

PORTAL_LABELS = {k: p.label for k, p in PORTALS.items()}

_SKIP = re.compile(r"^(foto|ilustrasi|grafis|infografik|video|baca juga)\b", re.I)


class Blocked(Exception):
    """Portal menolak permintaan (403/429/503)."""


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------
def _month_slug(keyword: str) -> str:
    return re.sub(r"\s+", "-", (keyword or "").strip().lower())


def _date_from_url(portal: Portal, url: str) -> date | None:
    m = portal.article_re.match(url)
    if not m or not m.groups():
        return None
    g = m.groups()
    try:
        if portal.key in ("cnn", "cnbc"):
            return datetime.strptime(g[0], "%Y%m%d%H%M%S").date()
        if portal.key in ("kompas", "suara"):
            return date(int(g[0]), int(g[1]), int(g[2]))
    except (ValueError, IndexError):
        return None
    return None


def _text_of(el) -> str:
    try:
        t = el.get_all_text(strip=True)
        if t:
            return t
    except Exception:
        pass
    try:
        return " ".join(el.css("::text").getall())
    except Exception:
        return ""


async def _document_only(route):
    if route.request.resource_type == "document":
        await route.continue_()
    else:
        await route.abort()


async def _light_setup(page):
    await page.route("**/*", _document_only)


async def _fetch(session, url: str, portal: Portal, *, light: bool = True):
    async def clear_routes(page):
        await page.unroute("**/*", _document_only)

    # Scrapling reuses tabs. Remove our route so it cannot affect the next page.
    options = {}
    if light and portal.light:
        options = {"page_setup": _light_setup, "page_action": clear_routes}
    resp = await session.fetch(url, **options)
    if resp is None:
        return None
    status = getattr(resp, "status", 200)
    if status in (403, 429, 503):
        raise Blocked(f"{portal.label} membalas HTTP {status}")
    if status >= 400:
        return None
    return resp


def _links_from(resp, portal: Portal) -> list[str]:
    """Kumpulkan URL artikel unik dari halaman daftar."""
    anchors = resp.css(portal.list_selector) if portal.list_selector else resp.css("a")
    links: dict[str, None] = {}
    for a in anchors:
        href = (a.attrib.get("href") or "").strip()
        if href and portal.article_re.match(href):
            links[href] = None
    return list(links)


# ---------------------------------------------------------------------------
# Penemuan (discover) — generator per halaman (interleaved dengan parsing)
# ---------------------------------------------------------------------------
def _kompas_url(keyword: str, start: str | None, end: str | None, page_no: int) -> str:
    url = f"https://search.kompas.com/search?q={quote(keyword)}&site_id=all"
    if start:
        url += f"&start_date={start}"
    if end:
        url += f"&end_date={end}"
    if page_no > 1:
        url += f"&page={page_no}"
    return url


def _detik_url(keyword: str, start: str | None, end: str | None, page_no: int) -> str:
    d1 = datetime.fromisoformat(start).strftime("%d/%m/%Y") if start else ""
    d2 = datetime.fromisoformat(end).strftime("%d/%m/%Y") if end else ""
    return (f"https://www.detik.com/search/searchall?query={quote(keyword)}&sortby=time"
            f"&fromdatex={d1}&todatex={d2}&page={page_no}")


async def _iter_search(portal: Portal, keyword: str, start, end, session, max_pages: int):
    seen: set[str] = set()
    for page_no in range(1, max_pages + 1):
        url = (_kompas_url(keyword, start, end, page_no) if portal.key == "kompas"
               else _detik_url(keyword, start, end, page_no))
        resp = await _fetch(session, url, portal)
        if resp is None:
            return
        if portal.key == "kompas":
            total_text = clean_ws(resp.css("#headArticle-count::text").get())
            if not re.sub(r"\D", "", total_text):
                return
            cards = resp.css(".articleList .articleItem")
            items = []
            for it in cards:
                link = (it.css("a.article-link::attr(href)").get() or "").strip()
                if not link or "/read/" not in link or not portal.article_re.match(link):
                    continue
                disp = clean_ws(it.css(".articlePost-date::text").get())
                d = _date_from_url(portal, link) or parse_date_text(disp)
                items.append({
                    "url": link,
                    "title": strip_title_suffix(clean_ws(it.css(".articleTitle::text").get()), portal),
                    "category": clean_ws(it.css(".articlePost-subtitle::text").get()),
                    "display_date": disp,
                    "date_iso": d.isoformat() if d else "",
                    "image": (it.css(".articleItem-img img::attr(src)").get() or "").strip(),
                    "snippet": clean_ws(it.css(".articleLead p::text").get()),
                })
            fresh = [item for item in items if item["url"] not in seen]
            if not fresh:
                return
            seen.update(item["url"] for item in fresh)
            yield fresh
            if len(cards) < 20:
                return
        else:
            links = _links_from(resp, portal)
            fresh = [url for url in links if url not in seen]
            if not fresh:
                return
            seen.update(fresh)
            yield [{"url": url} for url in fresh]
            if len(links) < 5:
                return
        await asyncio.sleep(0.4)


async def _iter_tag(portal: Portal, keyword: str, start, end, session, max_pages: int):
    slug = _month_slug(keyword)
    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    date_in_url = portal.key in ("cnn", "cnbc", "suara")
    # Batas halaman pengaman: arsip tag bisa sangat dalam (CNBC "emas" > 600
    # halaman). Untuk portal yang tanggalnya ada di URL, penghentian utama
    # adalah batas `start`; cap ini hanya jaring pengaman.
    if start_d:
        limit = min(max_pages, 500 if date_in_url else 150)
    else:  # tanpa tanggal awal: arsip tak terbatas, batasi agar tidak "stuck"
        limit = min(max_pages, 100 if date_in_url else 50)
    seen: set[str] = set()
    for page_no in range(1, limit + 1):
        url = f"{portal.base}/tag/{slug}"
        if page_no > 1:
            if portal.tag_page is None:
                return
            url += portal.tag_page.format(n=page_no)
        resp = await _fetch(session, url, portal)
        if resp is None:
            return
        links = _links_from(resp, portal)
        fresh = [u for u in links if u not in seen]
        if page_no > 1 and not fresh:
            return
        page_dates = [d for u in links if (d := _date_from_url(portal, u))]
        if start_d and page_dates and max(page_dates) < start_d:
            return  # seluruh halaman ini sudah lebih tua dari rentang
        items = []
        for u in fresh:
            seen.add(u)
            d = _date_from_url(portal, u)
            if d and start_d and d < start_d:
                continue
            if d and end_d and d > end_d:
                continue
            items.append({"url": u, "date_iso": d.isoformat() if d else ""})
        yield items
        if not links:
            return
        if start_d and page_dates and min(page_dates) < start_d:
            return  # halaman berikutnya pasti lebih tua
        await asyncio.sleep(0.4)


async def _iter_rss(portal: Portal, keyword: str, start, end, session, max_pages: int):
    items: list[dict] = []
    seen: set[str] = set()
    for feed in portal.feeds or []:
        # Chromium renders XML in a viewer. Read the original navigation
        # response so the XML parser receives the feed rather than its view.
        bodies = []
        raw_body = []

        def on_response(response):
            if response.request.resource_type == "document" and response.status == 200:
                bodies.append(response)

        async def capture_feed(page):
            page.on("response", on_response)

        async def read_feed(page):
            try:
                if bodies:
                    raw_body.append(await bodies[-1].body())
            finally:
                page.remove_listener("response", on_response)

        resp = await session.fetch(
            feed, page_setup=capture_feed, page_action=read_feed, google_search=False
        )
        if resp.status in (403, 429, 503):
            raise Blocked(f"{portal.label} membalas HTTP {resp.status}")
        if resp.status >= 400 or not raw_body:
            continue
        raw = raw_body[-1]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        try:
            root = ET.fromstring(raw or "")
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            if not link or not portal.article_re.match(link) or link in seen:
                continue
            try:
                pub = datetime.strptime((item.findtext("pubDate") or "").strip(),
                                        "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                pub = None
            if pub:
                d = pub.date()
                if start and d < date.fromisoformat(start):
                    continue
                if end and d > date.fromisoformat(end):
                    continue
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            seen.add(link)
            items.append({
                "url": link,
                "title": clean_ws(item.findtext("title")),
                "date_iso": pub.date().isoformat() if pub else "",
                "snippet": clean_ws(desc),
            })
    yield items


async def iter_discover(portal: Portal, query: str, start, end, session,
                  max_pages: int = 1000):
    """Yield daftar item per halaman (interleaved dengan parsing)."""
    discover = {"search": _iter_search, "rss": _iter_rss, "tag": _iter_tag}[portal.kind]
    async for items in discover(portal, query, start, end, session, max_pages):
        yield items


# ---------------------------------------------------------------------------
# Penguraian artikel (parse)
# ---------------------------------------------------------------------------
def _extract_paragraphs(resp, portal: Portal) -> list[str]:
    selectors = list(portal.body_selectors) + ["article p", "main p"]
    for sel in selectors:
        try:
            ps = resp.css(sel)
        except Exception:
            continue
        if len(ps) < 2:
            continue
        texts: list[str] = []
        for p in ps:
            t = clean_paragraph(_text_of(p), portal)
            if len(t) >= 30 and not _SKIP.search(t):
                texts.append(t)
        if len(texts) >= 2:
            return texts
    return []


async def parse(portal: Portal, url: str, session) -> dict | None:
    resp = await _fetch(session, url, portal)
    if resp is None:
        return None
    title = clean_ws(resp.css('meta[property="og:title"]::attr(content)').get()) \
        or clean_ws(resp.css("h1::text").get())
    title = strip_title_suffix(title, portal)

    date_iso = ""
    for blob in (resp.css('script[type="application/ld+json"]::text').getall() or []):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', blob)
        if m:
            d = parse_iso(m.group(1))
            if d:
                date_iso = d.isoformat()
                break
    if not date_iso:
        d = _date_from_url(portal, url)
        if d:
            date_iso = d.isoformat()

    paragraphs = _extract_paragraphs(resp, portal)
    content = "\n\n".join(paragraphs)
    return {
        "title": title,
        "date_iso": date_iso,
        "display_date": date_iso,
        "category": "",
        "image": (resp.css('meta[property="og:image"]::attr(content)').get() or "").strip(),
        "snippet": paragraphs[0][:300] if paragraphs else "",
        "content": content,
        "paragraphs": paragraphs,
    }
