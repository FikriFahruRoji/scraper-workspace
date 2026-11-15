import csv
import io
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from scrapling.parser import Selector

BASE = "https://scholar.google.com"
FIELDS = ("judul", "penulis", "tahun", "sumber", "sitasi", "tautan")
METRICS = ("total_sitasi", "h_index", "i10_index")


def search_url(query):
    return f"{BASE}/scholar?{urlencode({'q': query, 'hl': 'en'})}"


def profile_url(raw_url):
    parts = urlsplit(raw_url.strip())
    ids = parse_qs(parts.query).get("user", [])
    if (parts.scheme != "https" or parts.netloc != "scholar.google.com"
            or parts.path != "/citations" or len(ids) != 1
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ids[0])):
        raise ValueError("Masukkan link profil publik dari scholar.google.com.")
    return f"{BASE}/citations?{urlencode({'user': ids[0], 'hl': 'en'})}"


def content(node, rule):
    elements = node.css(rule)
    return " ".join(" ".join(elements[0].xpath(".//text()").getall()).split()) if elements else ""


def numeric(value):
    value = re.sub(r"[.,\s]", "", value)
    return int(value) if re.fullmatch(r"[0-9]+", value) else None


def link(node, rule, base):
    href = node.css(f"{rule}::attr(href)").get()
    if not href:
        return ""
    url = urljoin(base, href)
    return url if urlsplit(url).scheme in {"http", "https"} else ""


def parse(html, mode, url):
    page = Selector(html)
    if page.css("#gs_captcha_ccl, #captcha-form, iframe[src*='recaptcha'], form[action*='/sorry/']"):
        raise ValueError("Google Scholar menampilkan CAPTCHA.")
    rows = []
    metrics = []
    if mode == "Profil penulis":
        if not page.css("#gsc_a_b"):
            raise ValueError("HTML ini tidak memuat daftar publikasi profil.")
        canonical = page.css("link[rel=canonical]::attr(href)").get()
        if canonical and url != BASE and "/citations?" in canonical and profile_url(urljoin(BASE, canonical)) != url:
            raise ValueError("Profil di HTML berbeda dari tautan yang dimasukkan.")
        for row in page.css("#gsc_a_b .gsc_a_tr"):
            details = row.css(".gsc_a_t .gs_gray")
            rows.append(dict(zip(FIELDS, (
                content(row, ".gsc_a_at"),
                details[0].text.strip() if details else "",
                numeric(content(row, ".gsc_a_y")),
                details[1].text.strip() if len(details) > 1 else "",
                numeric(content(row, ".gsc_a_c a")),
                link(row, ".gsc_a_at", url),
            ))))
        headings = page.css("#gsc_rsb_st thead th")
        since = re.search(r"\b(?:19|20)\d{2}\b", headings[-1].text) if headings else None
        for name, row in zip(METRICS, page.css("#gsc_rsb_st tbody tr")):
            values = [numeric(cell.text) for cell in row.css(".gsc_rsb_std")]
            if values:
                metrics.append({"metrik": name, "semua": values[0],
                                "sejak_tahun": int(since[0]) if since else None,
                                "nilai_sejak_tahun": values[1] if len(values) > 1 else None})
        more = page.css("#gsc_bpf_more")
        return rows, metrics, bool(more and "disabled" not in more[0].attrib)

    if not page.css("#gs_res_ccl_mid"):
        raise ValueError("HTML ini tidak memuat halaman hasil pencarian Scholar.")
    for row in page.css("#gs_res_ccl_mid .gs_r.gs_or"):
        byline = content(row, ".gs_a")
        authors, separator, source = byline.partition(" - ")
        source = source.split(" - ", 1)[0] if separator else ""
        year = re.search(r"\b(?:18|19|20)\d{2}\b", source)
        citations = None
        for anchor in row.css(".gs_fl a"):
            href = anchor.attrib.get("href", "")
            if "cites" in parse_qs(urlsplit(href).query):
                match = re.search(r"[0-9][0-9,.]*", anchor.text)
                citations = numeric(match[0]) if match else None
                break
        title = re.sub(r"^\[[^\]]+\]\s*", "", content(row, ".gs_rt"))
        rows.append(dict(zip(FIELDS, (
            title, authors if separator else byline, int(year[0]) if year else None,
            source, citations, link(row, ".gs_rt a", url),
        ))))
    return rows, metrics, False


def unique(rows):
    seen = set()
    for row in rows:
        key = (row["judul"].casefold(), row["penulis"].casefold(), row["tahun"])
        if row["judul"] and key not in seen:
            seen.add(key)
            yield row


def csv_bytes(rows, fields):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: "'" + value if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@")
            ) else value
            for key, value in row.items()
        })
    return buffer.getvalue().encode("utf-8-sig")


def fetch_profile(url):
    from scrapling.fetchers import StealthySession

    url = profile_url(url)
    with StealthySession(headless=True, timeout=15000, retries=1,
                         solve_cloudflare=False, google_search=False) as session:
        response = session.fetch(url)
        if response.status != 200 or response.url != url:
            raise ValueError(f"Halaman profil tidak tersedia (HTTP {response.status}).")
        return response.body


def parse_profile_csv(data):
    """Read the bibliographic fields present in a Scholar profile export."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    headers = {name.strip().casefold(): name for name in reader.fieldnames or []}
    if "title" not in headers:
        raise ValueError("CSV tidak memiliki kolom Title. Pilih CSV dari menu Export di Scholar.")
    rows = []
    for row in reader:
        def field(*names):
            return next((value.strip() for name in names
                         if (value := row.get(headers.get(name)))), "")

        rows.append(dict(zip(FIELDS, (
            field("title"), field("authors", "author"), numeric(field("year", "publication year")),
            field("publication", "journal", "conference", "source", "book"),
            numeric(field("cited by", "citations")), field("url", "link"),
        ))))
    return rows
