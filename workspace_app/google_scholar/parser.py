"""Parsing halaman profil Google Scholar tanpa ketergantungan jaringan.

Semua fungsi di modul ini bekerja pada objek ``Selector``/``Response``
Scrapling atau HTML mentah, sehingga mudah diuji dengan fixture.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .errors import InvalidProfileError, ProfileNotFoundError
from .models import Article, CitationYear, Metrics, ProfileHeader, ScholarProfile

SCHOLAR_SCHEME = "https"
SCHOLAR_HOST = "scholar.google.com"
SCHOLAR_BASE = f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/citations"

# ID profil Scholar saat ini 12 karakter, tetapi rentang ini memberi ruang
# untuk variasi lama/baru tanpa menerima input sembarang.
ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
SCHOLAR_HOST_RE = re.compile(
    r"^scholar\.google\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2,3})$"
)
YEAR_RE = re.compile(r"(1[5-9]\d{2}|20\d{2}|21\d{2})")

SORT_VALUES = {"citations": None, "year": "pubdate", "title": "title"}


def clean_text(value: object) -> str:
    """Rapikan spasi, NBSP, dan pemisah baris dari teks HTML."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def parse_int(value: object) -> int | None:
    """Ambil bilangan bulat dari teks seperti ``1.234`` atau ``12,345``."""
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def extract_scholar_id(value: str) -> str:
    """Terima ID mentah atau URL profil, kembalikan ID yang tervalidasi.

    Hanya host ``scholar.google.*`` dan path ``/citations`` yang diterima.
    URL yang dibentuk ulang selalu memakai host Google Scholar, sehingga input
    pengguna tidak bisa dipakai untuk mengarahkan request ke host lain (SSRF).
    """
    raw = clean_text(value)
    if not raw:
        raise InvalidProfileError("URL atau ID profil Google Scholar wajib diisi.")

    if raw.lower().startswith("scholar.google."):
        raw = "https://" + raw
    if "user=" in raw and "://" not in raw:
        raw = raw.split("?", 1)[-1]

    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise InvalidProfileError("URL harus memakai http atau https.")
        host = (parsed.hostname or "").lower()
        if not SCHOLAR_HOST_RE.fullmatch(host):
            raise InvalidProfileError(
                "Host harus scholar.google.com (atau domain scholar.google.* resmi)."
            )
        if not parsed.path.rstrip("/").endswith("/citations"):
            raise InvalidProfileError(
                "URL harus halaman profil, contoh: "
                "https://scholar.google.com/citations?user=XXXXXXXXXXXX"
            )
        raw = clean_text(parse_qs(parsed.query).get("user", [""])[0])

    if not ID_RE.fullmatch(raw):
        raise InvalidProfileError(
            "ID profil tidak valid. Tempel URL lengkap atau ID 12 karakter "
            "dari parameter user=."
        )
    return raw


def build_profile_url(
    scholar_id: str,
    hl: str = "en",
    *,
    sort: str = "citations",
    cstart: int | None = None,
    pagesize: int = 100,
) -> str:
    """Susun URL request dari komponen yang sudah tervalidasi.

    ``cstart`` sengaja tidak diisi secara default karena robots.txt Google
    Scholar melarang URL ``/citations?*cstart=``.
    """
    sid = extract_scholar_id(scholar_id)
    params: dict[str, object] = {"user": sid, "hl": hl, "pagesize": pagesize}
    sort_value = SORT_VALUES.get(sort)
    if sort_value:
        params["view_op"] = "list_works"
        params["sortby"] = sort_value
    if cstart:
        params["cstart"] = int(cstart)
    return f"{SCHOLAR_BASE}?{urlencode(params)}"


def build_share_url(scholar_id: str, hl: str = "en") -> str:
    sid = extract_scholar_id(scholar_id)
    return f"{SCHOLAR_BASE}?{urlencode({'user': sid, 'hl': hl})}"


def detect_block(status: int | None, url: str, html: str, text: str = "") -> str | None:
    """Kembalikan alasan jika respons Scholar berupa blokir, bukan profil."""
    if status in {403, 429, 503}:
        return f"HTTP {status}"
    if "/sorry/" in (url or "").lower():
        return "halaman verifikasi Google"
    lowered_html = (html or "").lower()
    if (
        'id="gs_captcha_f"' in lowered_html
        or "gs_captcha" in lowered_html
        or "captcha-form" in lowered_html
    ):
        return "CAPTCHA"
    lowered_text = (text or "").lower()
    if "unusual traffic" in lowered_text or "not a robot" in lowered_text:
        return "CAPTCHA"
    return None


def is_login_redirect(url: str) -> bool:
    """True jika profil tidak ada/privat sehingga dialihkan ke login Google."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    return host == "accounts.google.com" or host.endswith(".accounts.google.com")


def parse_header(page) -> ProfileHeader:
    name = clean_text(page.css("#gsc_prf_in::text").get())
    affiliation = ""
    for node in page.css("#gsc_prf_i div.gsc_prf_il"):
        if node.attrib.get("id") in {"gsc_prf_ivh", "gsc_prf_int"}:
            continue
        affiliation = clean_text(node.get_all_text(separator="", strip=False))
        break

    email = ""
    homepage = ""
    email_node = page.css("#gsc_prf_ivh")
    if email_node:
        raw = clean_text(email_node[0].get_all_text(separator="", strip=False))
        email = raw.split(" - ")[0].strip()
        href = email_node[0].css("a::attr(href)").get()
        if href:
            homepage = urljoin(f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/", href)

    interests = tuple(
        cleaned
        for cleaned in (
            clean_text(item) for item in page.css("#gsc_prf_int a::text").getall()
        )
        if cleaned
    )
    return ProfileHeader(
        name=name,
        affiliation=affiliation,
        email=email,
        homepage=homepage,
        interests=interests,
    )


def parse_metrics(page) -> tuple[Metrics, Metrics, int | None]:
    """Baris metrik dibaca berdasarkan urutan: sitasi, h-index, i10-index."""
    rows = [
        row
        for row in page.css("#gsc_rsb_st tr")
        if row.css("td.gsc_rsb_std")
    ]
    values: list[list[int | None]] = []
    for row in rows:
        cells = row.css("td.gsc_rsb_std::text").getall()
        values.append([parse_int(cell) for cell in cells])
    while len(values) < 3:
        values.append([])

    def pick(row_index: int, column: int) -> int | None:
        row = values[row_index]
        return row[column] if len(row) > column else None

    metrics = Metrics(pick(0, 0), pick(1, 0), pick(2, 0))
    recent = Metrics(pick(0, 1), pick(1, 1), pick(2, 1))

    since_year = None
    headers = page.css("#gsc_rsb_st thead th::text").getall()
    for header in reversed(headers):
        match = YEAR_RE.search(str(header))
        if match:
            since_year = int(match.group(1))
            break
    return metrics, recent, since_year


def parse_articles(page, start_index: int = 1) -> list[Article]:
    articles: list[Article] = []
    for row in page.css("tr.gsc_a_tr"):
        title_node = row.css("a.gsc_a_at")
        if not title_node:
            continue
        title = clean_text(title_node[0].get_all_text(separator="", strip=False))
        if not title:
            continue

        href = title_node[0].attrib.get("href", "")
        article_url = urljoin(f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/", href) if href else ""
        publication_id = ""
        if article_url:
            citation_for_view = parse_qs(urlparse(article_url).query).get(
                "citation_for_view", [""]
            )[0]
            if citation_for_view:
                publication_id = citation_for_view.rsplit(":", 1)[-1]

        grays = row.css("div.gs_gray")
        authors = (
            clean_text(grays[0].get_all_text(separator="", strip=False))
            if len(grays) > 0
            else ""
        )
        venue = (
            clean_text(grays[1].get_all_text(separator="", strip=False))
            if len(grays) > 1
            else ""
        )

        raw_year = clean_text(row.css("span.gsc_a_h::text").get())
        year = parse_int(raw_year)
        if year is not None and not 1500 <= year <= 2200:
            year = None
        if year is not None:
            for suffix in (f", {year}", f" {year}"):
                if venue.endswith(suffix):
                    venue = venue[: -len(suffix)].rstrip(" ,")
                    break

        citation_nodes = row.css("a.gsc_a_ac")
        citations: int | None = None
        citations_url = ""
        if citation_nodes:
            citations_url = citation_nodes[0].attrib.get("href", "")
            if citations_url.startswith("/"):
                citations_url = urljoin(f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/", citations_url)
            raw_citations = clean_text(
                citation_nodes[0].get_all_text(separator="", strip=False)
            )
            citations = parse_int(raw_citations) or 0

        articles.append(
            Article(
                index=start_index + len(articles),
                title=title,
                authors=authors,
                venue=venue,
                year=year,
                citations=citations,
                article_url=article_url,
                citations_url=citations_url,
                publication_id=publication_id,
            )
        )
    return articles


def parse_citation_history(page) -> tuple[CitationYear, ...]:
    years = page.css("#gsc_rsb_cit span.gsc_g_t::text").getall()
    counts = page.css("#gsc_rsb_cit span.gsc_g_al::text").getall()
    history: list[CitationYear] = []
    for raw_year, raw_count in zip(years, counts):
        year = parse_int(raw_year)
        count = parse_int(raw_count)
        if year is not None and count is not None and 1900 <= year <= 2200:
            history.append(CitationYear(year=year, citations=count))
    return tuple(sorted(history, key=lambda item: item.year))


def page_has_more(page) -> bool:
    return bool(page.css("#gsc_bpf_more"))


def article_range_label(page) -> str:
    return clean_text(page.css("#gsc_a_nn::text").get())


def parse_profile_page(
    page,
    scholar_id: str,
    *,
    engine: str = "http",
    sort: str = "citations",
    pages_fetched: int = 1,
    has_more: bool | None = None,
    request_url: str = "",
    hl: str = "en",
) -> ScholarProfile:
    header = parse_header(page)
    if not header.name:
        raise ProfileNotFoundError(
            "Nama profil tidak ditemukan pada halaman. Profil mungkin privat, "
            "ID salah, atau halaman diblokir."
        )
    metrics, recent, since_year = parse_metrics(page)
    articles = parse_articles(page)
    if has_more is None:
        has_more = page_has_more(page)
    return ScholarProfile(
        scholar_id=extract_scholar_id(scholar_id),
        name=header.name,
        affiliation=header.affiliation,
        email=header.email,
        homepage=header.homepage,
        interests=header.interests,
        metrics=metrics,
        recent_metrics=recent,
        recent_since_year=since_year,
        citation_history=parse_citation_history(page),
        articles=tuple(articles),
        source_url=build_share_url(scholar_id, hl=hl),
        request_url=request_url or build_profile_url(scholar_id, hl=hl, sort=sort),
        fetched_at=datetime.now(timezone.utc),
        engine=engine,
        sort=sort,
        pages_fetched=pages_fetched,
        has_more=bool(has_more),
    )
