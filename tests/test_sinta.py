from pathlib import Path
from threading import Event

import pytest
from scrapling.parser import Selector
from streamlit.testing.v1 import AppTest

from workspace_app import sources
from workspace_app.sinta_view import check

FIXTURES = Path(__file__).parent / "fixtures" / "sinta"
sinta = sources.load("sinta")


def page(name):
    return Selector((FIXTURES / f"{name}.html").read_text())


def test_numbers_and_ids_follow_sinta_formats():
    assert sinta.number("21.258") == 21258 and sinta.number("1.301") == 1301 and sinta.number("0") == 0
    assert sinta.number("14487") == 14487 and sinta.number("23 cited") == 23
    assert sinta.number("-") is None and sinta.number(None) is None
    ids = ["417", " 5974504 ", "https://sinta.kemdiktisaintek.go.id/authors/profile/5974504/?view=scopus",
           "https://sinta.kemdiktisaintek.go.id/affiliations/profile/414", "/affiliations/authors/417", "abc", ""]
    assert [sinta.extract_id(text) for text in ids] == ["417", "5974504", "5974504", "414", "417", None, None]


def test_affiliation_page_gives_authors_with_scores():
    name, total, pages, rows = sinta.parse_affiliation_page(page("affiliation_authors"))
    assert (name, total, pages) == ("Institut Teknologi Sepuluh Nopember", 1301, 131)
    assert len(rows) == 3
    first = rows[0]
    assert first["id_sinta"] == "29555" and first["nama"] == "RIYANARTO SARNO"
    assert first["program_studi"] == "Ilmu Komputer (S3)"
    assert (first["h_index_scopus"], first["h_index_gs"]) == (36, 46)
    assert (first["skor_sinta"], first["skor_sinta_3thn"]) == (21258, 1828)  # dots are thousands, not decimals
    assert first["url"].endswith("/authors/profile/29555")


def test_profile_page_gives_scores_and_the_three_metric_columns():
    row = sinta.parse_profile(page("author_profile"), 5974504)
    assert row["nama"] == "ASEP BAYU DANI NANDIYANTO" and row["afiliasi"] == "Universitas Pendidikan Indonesia"
    assert row["id_afiliasi"] == "414" and row["program_studi"] == "S1 - Teknik Kimia"
    assert row["bidang"].startswith("Chemistry; Chemical Engineering")
    assert (row["skor_sinta"], row["skor_sinta_3thn"]) == (17721, 2265)  # the profile says "SINTA Score Overall"
    assert (row["scopus_h_index"], row["gscholar_h_index"], row["wos_h_index"]) == (54, 65, 33)
    assert (row["scopus_sitasi"], row["gscholar_sitasi"], row["wos_artikel"]) == (14487, 23722, 262)
    assert sinta.parse_profile(Selector("<html><body><h1>SINTA home</h1></body></html>"), 1) is None


@pytest.mark.parametrize("view, publisher, year, cited", [
    ("scopus", "Asean Journal for Science and Engineering in Materials", 2026, 23),
    ("googlescholar", "AIP Conference Proceedings 3464 (1), 090005, 2026", 2026, 1),
    ("researches", "Penelitian Kompetitif Nasional ( PFR )", 2025, None)])
def test_document_rows_are_labelled_as_a_sample(view, publisher, year, cited):
    rows = sinta.parse_documents(page(f"documents_{view}"), 5974504, view)
    assert len(rows) == 2
    row = rows[0]
    assert (row["penerbit"], row["tahun"], row["sitasi"]) == (publisher, year, cited)
    assert row["daftar"] == sinta.DOCUMENT_VIEWS[view] and row["judul"]
    assert "10 dokumen acak" in row["catatan"]  # every row says it is a sample


class FakeClient:
    """Stands in for the browser-like session: serves fixtures by URL path."""

    def __init__(self, stop, log, **_):
        self.paths = []
        FakeClient.last = self

    def close(self):
        pass

    def page(self, path):
        self.paths.append(path)
        if path.startswith("/affiliations/authors/417"):
            return page("affiliation_authors")
        if "view=" in path and "5974504" in path:
            body = lambda name: (FIXTURES / name).read_text().split("<body>")[1].split("</body>")[0]  # noqa: E731
            return Selector("<html><body>" + body("author_profile.html") + body("documents_scopus.html")
                            + "</body></html>")  # the profile header plus the document items
        if "/authors/profile/5974504" in path:
            return page("author_profile")
        return Selector("<html><body><h1>SINTA home</h1></body></html>")  # unknown IDs show the home page


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(sinta, "_Client", FakeClient)


def test_affiliation_job_stops_at_the_requested_authors_and_reports_unknown_ids(fake):
    result = sinta.collect_sinta("affiliation", ["417", "https://sinta.kemdiktisaintek.go.id/affiliations/profile/9"],
                                 max_authors=2, full_profile=False)
    assert len(result["rows"]) == 2 and result["rows"][0]["id_sinta"] == "29555" and result["rows"][0]["afiliasi"] == "Institut Teknologi Sepuluh Nopember"
    assert result["rows"][0]["id_afiliasi"] == "417"
    assert "9" in result["error"] and "tidak ditemukan" in result["error"]  # valid rows are kept
    assert FakeClient.last.paths[0] == "/affiliations/authors/417?page=1"


def test_affiliation_job_walks_every_page_and_drops_repeated_authors(fake):
    result = sinta.collect_sinta("affiliation", ["417"], max_authors=0, full_profile=False)
    # the fixture serves the same 3 authors for each of the 131 pages: repeats must not become extra rows
    assert len(result["rows"]) == 3 and len(FakeClient.last.paths) == 131 and result["error"] is None


def test_profile_job_keeps_found_authors_and_names_the_missing(fake):
    result = sinta.collect_sinta("profile", ["5974504", "123456789", "5974504"])
    assert [row["id_sinta"] for row in result["rows"]] == ["5974504"]  # the repeated ID is fetched once
    assert result["error"] == "ID penulis tidak ditemukan: 123456789."
    none = sinta.collect_sinta("profile", ["123456789"])
    assert none["rows"] == [] and none["error"].startswith("Tidak ada data")


def test_documents_job_tags_each_row_with_its_list(fake):
    result = sinta.collect_sinta("documents", ["5974504"], views=["scopus", "googlescholar"])
    labels = [row["daftar"] for row in result["rows"]]
    assert labels == ["Scopus", "Scopus", "Google Scholar", "Google Scholar"]  # one list at a time, in the order asked


def test_time_limit_and_stop_keep_what_was_collected(fake, monkeypatch):
    result = sinta.collect_sinta("affiliation", ["417"], max_authors=0, max_minutes=0, full_profile=False)
    assert result["stop_reason"] == "waktu" and "Batas waktu" in result["message"]
    stop = Event()
    stop.set()

    class StoppedClient(FakeClient):
        def page(self, path):
            raise InterruptedError("Dihentikan.")

    monkeypatch.setattr(sinta, "_Client", StoppedClient)
    stopped = sinta.collect_sinta("profile", ["5974504"], stop_event=stop)
    assert stopped["rows"] == [] and stopped["error"] == "Dihentikan."


def test_sinta_executes_through_the_workspace_source_registry(fake):
    rows, metadata, error = sources.execute(
        "sinta", {"mode": "profile", "targets": ["5974504"], "max_authors": 0, "views": [], "max_minutes": 1},
        None, Event(), lambda message: None, lambda rows, **kwargs: None)
    assert len(rows) == 1 and error is None and metadata["sumber"].startswith("SINTA")


def test_form_validation_and_rerun_seed(tmp_path):
    from workspace_app.views import validate
    base = {"mode": "profile", "targets": ["5974504"], "max_authors": 0, "views": [], "max_minutes": 5}
    validate("sinta", base)
    for change, text in ((dict(targets=[]), "minimal satu"), (dict(targets=["bukan id"]), "valid"),
                         (dict(targets=[str(n) for n in range(101)]), "Maksimal"),
                         (dict(mode="documents", targets=["1"], views=[]), "daftar dokumen"),
                         (dict(mode="affiliation", targets=["417"], max_authors=99999), "Maksimum penulis")):
        with pytest.raises(ValueError, match=text):
            check({**base, **change})

    script = tmp_path / "page.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import streamlit as st\n"
        "from workspace_app.views import scraper_form, seed_form\n"
        "from workspace_app.jobs import JobManager\n"
        "if 'seeded' not in st.session_state:\n"
        "    st.session_state['seeded'] = True\n"
        "    seed_form('sinta', {'mode': 'documents', 'targets': ['5974504', '29555'], 'views': ['garuda'],\n"
        "                        'max_minutes': 5}, 'Uji')\n"
        f"scraper_form(JobManager({str(tmp_path / 'data')!r}), 'sinta', None, {{}})\n")
    at = AppTest.from_file(str(script), default_timeout=30).run()
    assert not at.exception
    assert at.text_area(key="f-sinta-targets-documents").value == "5974504\n29555"
    assert at.multiselect(key="f-sinta-views").value == ["garuda"]
    assert at.radio(key="f-sinta-mode").value == "documents"


def test_result_charts_render_for_each_kind_of_result(tmp_path):
    script = tmp_path / "page.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import pandas as pd\n"
        "from workspace_app.sinta_view import chart\n"
        "authors = pd.DataFrame({'nama': ['A', 'B'], 'skor_sinta': [21258, None], 'scopus_h_index': [36, None],\n"
        "                        'gscholar_h_index': [46, None], 'wos_h_index': [None, None]})\n"
        "profiles = pd.DataFrame({'nama': ['A', 'B'], 'scopus_h_index': [54, 36], 'gscholar_h_index': [65, 46],\n"
        "                         'wos_h_index': [33, None], 'skor_sinta': [17721, 21258]})\n"
        "documents = pd.DataFrame({'judul': ['x', 'y'], 'daftar': ['Scopus', 'Garuda']})\n"
        "empty = pd.DataFrame({'nama': ['A'], 'skor_sinta': [None], 'scopus_h_index': [None],\n"
        "                      'gscholar_h_index': [None], 'wos_h_index': [None]})\n"
        "old = pd.DataFrame({'nama': ['A'], 'skor_sinta': [900], 'h_index_scopus': [3], 'h_index_gs': [7]})\n"
        "for frame in (authors, profiles, documents, empty, old):\n"
        "    chart(frame, 'sinta')\n")
    at = AppTest.from_file(str(script), default_timeout=30).run()
    assert not at.exception
    assert [metric.value for metric in at.metric][:3] == ["2", "21.258", "36"]
    assert [metric.value for metric in at.metric][-6:-3] == ["1", "–", "–"]  # no scores yet: a dash, not a crash
    assert [metric.value for metric in at.metric][-3:] == ["1", "900", "3"]  # a dataset saved with the old columns


def test_affiliation_rows_share_the_profile_table_columns(fake):
    quick = sinta.collect_sinta("affiliation", ["417"], max_authors=2, full_profile=False)
    profile = sinta.collect_sinta("profile", ["5974504"])
    assert list(quick["rows"][0]) == list(profile["rows"][0]) == sinta.PROFILE_COLUMNS
    first = quick["rows"][0]
    assert first["scopus_h_index"] == 36 and first["gscholar_h_index"] == 46  # the list shows these two
    assert first["scopus_sitasi"] is None and first["bidang"] is None  # what only a profile has stays empty


def test_full_profile_fills_the_metrics_and_keeps_authors_whose_profile_is_missing(fake):
    result = sinta.collect_sinta("affiliation", ["417"], max_authors=3, full_profile=True)
    assert [row["id_sinta"] for row in result["rows"]] == ["29555", result["rows"][1]["id_sinta"], result["rows"][2]["id_sinta"]]
    assert all(list(row) == sinta.PROFILE_COLUMNS for row in result["rows"])
    assert FakeClient.last.paths[-3:] == [f"/authors/profile/{row['id_sinta']}" for row in result["rows"]]
    # the fake knows only one real profile (5974504); the list authors have none, so they keep their list data
    assert result["rows"][0]["nama"] == "RIYANARTO SARNO" and result["rows"][0]["skor_sinta"] == 21258


def test_full_profile_replaces_list_data_with_the_profile_when_it_opens(monkeypatch):
    class EveryProfileOpens(FakeClient):
        def page(self, path):
            if path.startswith("/authors/profile/"):
                self.paths.append(path)
                return page("author_profile")
            return super().page(path)

    monkeypatch.setattr(sinta, "_Client", EveryProfileOpens)
    result = sinta.collect_sinta("affiliation", ["417"], max_authors=2, full_profile=True)
    row = result["rows"][0]
    assert row["bidang"].startswith("Chemistry") and row["scopus_sitasi"] == 14487 and row["wos_h_index"] == 33
    assert list(row) == sinta.PROFILE_COLUMNS
