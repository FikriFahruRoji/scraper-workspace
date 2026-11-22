"""SINTA (sinta.kemdiktisaintek.go.id): authors of an affiliation, author profiles, and a sample of their documents.

Rebuilt for the current SINTA site, because the `sinta-scraper` 1.2.0 package still targets the retired
domain and markup. What the public site really offers decides the modes:

- affiliation: every author of an affiliation, paginated and stable ("Page 1 of 131").
- profile: one author's header, scores and the Scopus / Google Scholar / WoS metrics table.
- documents: the author's document lists show only 10 items, with no pagination, and the items change from
  one request to the next. The rows are therefore a sample and every row says so.

SINTA answers non-browser clients with 403, so requests go through Scrapling's Chrome-impersonating session.
"""
from math import ceil
import re
from time import monotonic

from scrapling.fetchers import FetcherSession
from scrapling.parser import Selector

BASE = "https://sinta.kemdiktisaintek.go.id"
PAGE_SIZE = 10  # authors per page of an affiliation
DOCUMENT_VIEWS = {"scopus": "Scopus", "googlescholar": "Google Scholar", "garuda": "Garuda",
                  "researches": "Penelitian"}
SAMPLE_NOTE = "Contoh: SINTA hanya menampilkan 10 dokumen acak, bukan daftar lengkap."
# Author lists say "SINTA Score"; the profile page says "SINTA Score Overall".
SCORE_LABELS = {"SINTA Score 3Yr": "skor_sinta_3thn", "SINTA Score": "skor_sinta", "SINTA Score Overall": "skor_sinta",
                "Affil Score 3Yr": "skor_afiliasi_3thn", "Affil Score": "skor_afiliasi"}
METRICS = {"Article": "artikel", "Citation": "sitasi", "Cited Document": "dokumen_tersitasi",
           "H-Index": "h_index", "i10-Index": "i10_index", "G-Index": "g_index"}
SOURCES = {"Scopus": "scopus", "GScholar": "gscholar", "WOS": "wos"}
NUMBER = re.compile(r"\d[\d.,]*")


class SintaError(Exception):
    """A failure worth showing to the user as is."""


def number(text):
    """An integer from SINTA's text; thousands are separated by dots ("21.258" is 21258). None when empty."""
    match = NUMBER.search(str(text or ""))
    return int(re.sub(r"[.,]", "", match.group())) if match else None


def extract_id(text):
    """The numeric SINTA ID from a bare number or a SINTA profile URL; None when there is none."""
    text = str(text).strip()
    if text.isdigit():
        return text
    match = re.search(r"/(?:authors|affiliations)/(?:profile|authors|detail)/(\d+)", text) \
        or re.search(r"[?&]id=(\d+)", text)
    return match.group(1) if match else None


def _text(element, separator=" "):
    return " ".join(element.get_all_text(separator=separator, strip=True).split())


def _first(selection):
    return selection[0] if len(selection) else None


def _stat_scores(block):
    scores = {name: None for name in SCORE_LABELS.values()}
    for value, label in zip(block.css(".stat-num"), block.css(".stat-text")):
        if name := SCORE_LABELS.get(_text(label)):
            scores[name] = number(_text(value))
    return scores


def parse_affiliation_page(page):
    """(affiliation name, total authors, total pages, author rows) from one affiliation author list page."""
    name = _first(page.css("h3"))
    text = _text(page)
    pages = re.search(r"Page\s+\d+\s+of\s+(\d+)\s*\|\s*Total Records\s*([\d.,]+)", text)
    rows = []
    for block in page.xpath("//div[div[contains(@class,'profile-name')]]"):
        link = _first(block.css(".profile-name a"))
        author_id = extract_id(link.attrib.get("href", "")) if link else None
        if not author_id:
            continue
        dept = _first(block.css(".profile-dept"))
        hindex = _text(block.css(".profile-hindex")[0]) if len(block.css(".profile-hindex")) else ""
        scopus, scholar = re.search(r"Scopus H-Index\s*:\s*(\d+)", hindex), re.search(r"GS H-Index\s*:\s*(\d+)", hindex)
        rows.append({
            "id_sinta": author_id, "nama": _text(link),
            "program_studi": _text(dept) if dept else "",
            "h_index_scopus": int(scopus.group(1)) if scopus else None,
            "h_index_gs": int(scholar.group(1)) if scholar else None,
            **_stat_scores(block), "url": f"{BASE}/authors/profile/{author_id}",
        })
    return (_text(name) if name else None, number(pages.group(2)) if pages else None,
            int(pages.group(1)) if pages else None, rows)


def parse_profile(page, author_id):
    """One author's profile as a flat row; None when the page is not an author profile (SINTA shows the home page)."""
    heading, meta = _first(page.css("h3")), _first(page.css(".meta-profile"))
    if heading is None or meta is None:
        return None
    affiliation = _first(meta.xpath(".//a[contains(@href,'/affiliations/profile/')]"))
    dept = _first(meta.xpath(".//a[contains(@href,'/departments/')]"))
    row = {
        "id_sinta": str(author_id), "nama": _text(heading),
        "afiliasi": _text(affiliation) if affiliation else "",
        "id_afiliasi": extract_id(affiliation.attrib.get("href", "")) if affiliation else None,
        "program_studi": _text(dept) if dept else "",
        "bidang": "; ".join(_text(link) for link in page.css(".subject-list a")),
    }
    row.update({name: None for name in SCORE_LABELS.values()})
    for value, label in zip(page.css(".pr-num"), page.css(".pr-txt")):
        if name := SCORE_LABELS.get(_text(label)):
            row[name] = number(_text(value))
    for source in SOURCES.values():
        row.update({f"{source}_{metric}": None for metric in METRICS.values()})
    for table in page.css("table"):
        header = [_text(cell) for cell in table.css("tr")[0].css("th, td")] if len(table.css("tr")) else []
        if not set(SOURCES) <= set(header):
            continue
        for tr in table.css("tr")[1:]:
            cells = [_text(cell) for cell in tr.css("th, td")]
            if cells and cells[0] in METRICS:
                for source, value in zip(header[1:], cells[1:]):
                    if source in SOURCES:
                        row[f"{SOURCES[source]}_{METRICS[cells[0]]}"] = number(value)
        break
    row["url"] = f"{BASE}/authors/profile/{author_id}"
    return row


def parse_documents(page, author_id, view):
    """The document items on one author document list page, as rows tagged with the list they came from."""
    rows = []
    for item in page.css(".ar-list-item"):
        title = _first(item.css(".ar-title a"))
        if title is None:
            continue
        link = title.attrib.get("href", "")
        row = {"id_sinta": str(author_id), "daftar": DOCUMENT_VIEWS[view], "judul": _text(title),
               "tautan": "" if link in ("", "#!") else link, "kategori": "", "penerbit": "", "tahun": None,
               "sitasi": None, "keterangan": ""}
        notes = []
        for anchor in item.css(".ar-meta a"):
            kind, text = anchor.attrib.get("class", ""), _text(anchor)
            if "ar-quartile" in kind:
                row["kategori"] = " · ".join(part for part in (row["kategori"], text) if part)
            elif "ar-pub" in kind:
                row["penerbit"] = text
            elif "ar-year" in kind:
                row["tahun"] = number(text)
            elif "ar-cited" in kind:
                row["sitasi"] = number(text)
            elif text:
                notes.append(text)
        row["keterangan"] = " | ".join(notes)
        row["catatan"] = SAMPLE_NOTE
        rows.append(row)
    return rows


PROFILE_COLUMNS = (["id_sinta", "nama", "afiliasi", "id_afiliasi", "program_studi", "bidang"]
                   + list(dict.fromkeys(SCORE_LABELS.values()))
                   + [f"{source}_{metric}" for source in SOURCES.values() for metric in METRICS.values()] + ["url"])


def _as_profile_row(found, affiliation, affiliation_id):
    """An author-list row in the profile table's columns. What the list does not show stays empty."""
    row = {column: None for column in PROFILE_COLUMNS}
    row.update({column: found[column] for column in ("id_sinta", "nama", "program_studi", "url", *SCORE_LABELS.values())})
    row.update(afiliasi=affiliation, id_afiliasi=affiliation_id,
               scopus_h_index=found["h_index_scopus"], gscholar_h_index=found["h_index_gs"])
    return row


class _Client:
    """A polite, stoppable fetcher: one browser-like session, a pause between requests, plain errors."""

    def __init__(self, stop, log, pause=1.0, timeout=30):
        self.stop, self.log, self.pause = stop, log, pause
        self.session = FetcherSession(impersonate="chrome", timeout=timeout, retries=2)
        self.client = self.session.__enter__()  # the entered session is the object that can fetch
        self.fetched = False

    def close(self):
        self.session.__exit__(None, None, None)

    def page(self, path):
        if self.fetched and self.stop is not None and self.stop.wait(self.pause):
            raise InterruptedError("Dihentikan.")
        if self.stop is not None and self.stop.is_set():
            raise InterruptedError("Dihentikan.")
        try:
            response = self.client.get(f"{BASE}{path}")
        except Exception as exc:  # network and TLS failures arrive as library-specific exceptions
            raise SintaError(f"Tidak bisa menghubungi SINTA ({type(exc).__name__}). Periksa koneksi internet.") from exc
        self.fetched = True
        if response.status == 403:
            raise SintaError("SINTA menolak permintaan (403). Coba lagi beberapa menit kemudian.")
        if response.status != 200:
            raise SintaError(f"SINTA menjawab {response.status}.")
        return Selector(response.html_content if isinstance(response.html_content, str) else str(response.html_content))


def _collect_affiliations(client, ids, max_authors, full_profile, expired, log, checkpoint):
    """Authors of each affiliation in the profile table's columns. With `full_profile`, every author's own
    profile is then opened to fill the Scopus, Google Scholar and WoS metrics and the research fields."""
    rows, seen, missing = [], set(), []
    for number_, affiliation_id in enumerate(ids, 1):
        wanted_pages, page_number, total_pages, name = None, 1, None, None
        log(f"Afiliasi {number_} dari {len(ids)} (ID {affiliation_id})")
        while total_pages is None or page_number <= min(total_pages, wanted_pages or total_pages):
            if expired():
                return rows, missing, "waktu"
            name_, total, pages, found = parse_affiliation_page(
                client.page(f"/affiliations/authors/{affiliation_id}?page={page_number}"))
            if total_pages is None:
                if not pages or not found:
                    missing.append(affiliation_id)
                    log("  tidak ditemukan atau belum punya penulis")
                    break
                name, total_pages = name_, pages
                wanted_pages = ceil(max_authors / PAGE_SIZE) if max_authors else None
                log(f"  {name}: {total or '?'} penulis, {total_pages} halaman")
            for row in found:
                if row["id_sinta"] not in seen:
                    seen.add(row["id_sinta"])
                    rows.append(_as_profile_row(row, name, affiliation_id))
            if max_authors and len(rows) >= max_authors:
                del rows[max_authors:]
                break
            log(f"  halaman {page_number} dari {min(total_pages, wanted_pages or total_pages)}: {len(rows)} penulis")
            checkpoint(rows)
            page_number += 1
    if full_profile:
        for index, row in enumerate(rows):
            if expired():
                return rows, missing, "waktu"
            profile = parse_profile(client.page(f"/authors/profile/{row['id_sinta']}"), row["id_sinta"])
            if profile is None:
                log(f"  profil {row['id_sinta']} tidak bisa dibuka; kolom metrik dibiarkan kosong")
            else:
                rows[index] = {column: profile.get(column) for column in PROFILE_COLUMNS}
            if (index + 1) % 10 == 0 or index + 1 == len(rows):
                log(f"Profil lengkap {index + 1} dari {len(rows)} penulis")
                checkpoint(rows)
    return rows, missing, None


def _collect_profiles(client, ids, expired, log, checkpoint):
    rows, missing = [], []
    for number_, author_id in enumerate(ids, 1):
        if expired():
            return rows, missing, "waktu"
        row = parse_profile(client.page(f"/authors/profile/{author_id}"), author_id)
        if row is None:
            missing.append(author_id)
            log(f"Penulis {number_} dari {len(ids)} (ID {author_id}): tidak ditemukan")
            continue
        rows.append(row)
        log(f"Penulis {number_} dari {len(ids)}: {row['nama']}")
        checkpoint(rows)
    return rows, missing, None


def _collect_documents(client, ids, views, expired, log, checkpoint):
    rows, missing = [], []
    for number_, author_id in enumerate(ids, 1):
        for view in views:
            if expired():
                return rows, missing, "waktu"
            page = client.page(f"/authors/profile/{author_id}/?view={view}")
            if _first(page.css(".meta-profile")) is None:
                missing.append(author_id)
                log(f"Penulis {number_} dari {len(ids)} (ID {author_id}): tidak ditemukan")
                break
            found = parse_documents(page, author_id, view)
            rows.extend(found)
            log(f"Penulis {number_} dari {len(ids)} · {DOCUMENT_VIEWS[view]}: {len(found)} dokumen contoh")
            checkpoint(rows)
    return rows, missing, None


def collect_sinta(mode, targets, max_authors=0, views=("scopus", "googlescholar"), max_minutes=10,
                  full_profile=True, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Run one SINTA job. `targets` are SINTA IDs or profile URLs: affiliations for "affiliation", authors otherwise."""
    log = log_cb or (lambda message: None)
    checkpoint = checkpoint_cb or (lambda rows: None)
    ids = list(dict.fromkeys(found for found in (extract_id(target) for target in targets) if found))
    deadline = monotonic() + max_minutes * 60
    expired = lambda: monotonic() > deadline  # noqa: E731
    rows, missing, stopped, error = [], [], None, None
    client = _Client(stop_event, log)
    try:
        if mode == "affiliation":
            rows, missing, stopped = _collect_affiliations(client, ids, max_authors, full_profile, expired, log, checkpoint)
        elif mode == "profile":
            rows, missing, stopped = _collect_profiles(client, ids, expired, log, checkpoint)
        elif mode == "documents":
            rows, missing, stopped = _collect_documents(client, ids, views, expired, log, checkpoint)
        else:
            raise SintaError(f"Mode tidak dikenal: {mode}.")
    except InterruptedError as exc:
        error = str(exc)
    except SintaError as exc:
        error = str(exc)
        log(f"Gagal: {exc}")
    finally:
        client.close()
    if missing and not error:
        kind = "afiliasi" if mode == "affiliation" else "penulis"
        error = f"ID {kind} tidak ditemukan: {', '.join(missing)}." if rows else \
            f"Tidak ada data. ID {kind} tidak ditemukan: {', '.join(missing)}."
    result = {"rows": rows, "error": error, "sumber": "SINTA (sinta.kemdiktisaintek.go.id)"}
    if stopped == "waktu":
        result.update(stop_reason="waktu", message="Batas waktu tercapai; data yang sudah terkumpul disimpan.")
    elif not rows and not error:
        result["error"] = "Tidak ada data untuk ID ini."
    log(f"{len(rows)} baris.")
    return result
