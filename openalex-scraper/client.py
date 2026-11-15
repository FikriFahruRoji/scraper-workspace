import csv
import io
import re
from urllib.parse import urlsplit

from scrapling.fetchers import StealthySession
from scrapling.parser import Selector

BASE = "https://openalex.org"
FIELDS = ("id_openalex", "judul", "penulis", "tahun", "sumber", "sitasi", "tipe", "doi", "tautan")


def author_id(value):
    value = value.strip()
    if "://" in value:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.netloc not in {
            "openalex.org", "www.openalex.org", "api.openalex.org"
        }:
            raise ValueError("Gunakan tautan profil OpenAlex atau ID penulis seperti A123456.")
        match = re.fullmatch(r"/(?:authors/)?(A[0-9]+)/?", parts.path, re.I)
        value = match[1] if match else ""
    if not re.fullmatch(r"A[0-9]+", value, re.I):
        raise ValueError("ID penulis OpenAlex harus diawali A, diikuti angka.")
    return value.upper()


def as_csv(rows, columns):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(
            ("=", "+", "-", "@")) else v for k, v in row.items()})
    return buffer.getvalue().encode("utf-8-sig")


def number(text):
    match = re.search(r"\d[\d,]*", text or "")
    return int(match[0].replace(",", "")) if match else None


def text(node):
    return " ".join(" ".join(node.xpath(".//text()").getall()).split())


def profile_metrics(page, identifier):
    metrics = {"id_openalex": f"{BASE}/{identifier}"}
    title = page.css("main h1")
    if title:
        metrics["penulis"] = text(title[0])
    for label, name in {
        "Works count:": "publikasi_profil",
        "Citation count:": "total_sitasi",
        "H-index:": "h_index",
        "I10-index:": "i10_index",
    }.items():
        labels = page.xpath(f"//main//span[normalize-space(.)='{label}']")
        if labels:
            values = labels[0].xpath("following-sibling::span[1]")
            metrics[name] = number(text(values[0])) if values else None
    if metrics.get("publikasi_profil") is None:
        raise ValueError("Metrik profil belum tampil atau format halaman berubah.")
    return metrics


def fetch_profile(identifier):
    url = f"{BASE}/authors/{author_id(identifier)}"
    with StealthySession(headless=True, google_search=False, solve_cloudflare=False,
                         timeout=15000, retries=1) as session:
        page = session.fetch(url, wait_selector="main h1", wait=750)
    if page.status != 200 or page.url.rstrip("/") != url:
        raise ValueError(f"Halaman profil tidak tersedia (HTTP {page.status}).")
    return profile_metrics(page, identifier)


def work_id(value):
    parts = urlsplit(value)
    if parts.netloc and parts.netloc.lower() not in {"openalex.org", "www.openalex.org"}:
        return ""
    match = re.fullmatch(r"/?(?:works/)?(W\d+)/?", parts.path, re.I)
    return f"{BASE}/{match[1].upper()}" if match else ""


def read_works(data):
    page = Selector(data)
    rows = []
    for anchor in page.css("main a[href], article a[href], table a[href]"):
        identifier = work_id(anchor.attrib["href"])
        title = text(anchor)
        if not identifier or not title or title == identifier.rsplit("/", 1)[-1]:
            continue
        containers = anchor.xpath("ancestor::*[self::tr or self::article or @role='row'][1]")
        scope = containers[0] if containers else anchor
        row = dict.fromkeys(FIELDS)
        row.update(id_openalex=identifier, judul=title, tautan=identifier)
        for cell in scope.css("[data-label]"):
            label = cell.attrib["data-label"].strip().casefold()
            value = text(cell)
            if label in {"year", "publication year"}:
                row["tahun"] = number(value)
            elif label in {"citations", "cited by", "citation count"}:
                row["sitasi"] = number(value)
            elif label in {"authors", "author"}:
                row["penulis"] = value
            elif label in {"source", "journal"}:
                row["sumber"] = value
            elif label == "type":
                row["tipe"] = value
        for link in scope.css('a[href^="https://doi.org/"]'):
            row["doi"] = link.attrib["href"]
            break
        rows.append(row)
    return rows


def read_csv(data):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    columns = {name.strip().casefold(): name for name in (reader.fieldnames or [])}
    if not ({"title", "display_name", "judul"} & columns.keys()):
        raise ValueError("CSV tidak memuat kolom judul publikasi.")
    rows = []
    for record in reader:
        def get(*names):
            return next((record[columns[name]].strip() for name in names
                         if name in columns and record.get(columns[name])), "")

        identifier = work_id(get("id", "openalex id", "openalex_id", "id_openalex"))
        doi = get("doi")
        rows.append(dict(zip(FIELDS, (
            identifier, get("title", "display_name", "judul"), get("authors", "penulis"),
            number(get("publication_year", "publication year", "year", "tahun")),
            get("source", "source name", "primary_location.source.display_name", "sumber"),
            number(get("cited_by_count", "cited by count", "citations", "sitasi")),
            get("type", "tipe"), doi, get("url", "tautan") or doi or identifier,
        ))))
    return rows
