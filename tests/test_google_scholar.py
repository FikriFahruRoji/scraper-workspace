from contextlib import nullcontext
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
import pytest
from scrapling.parser import Selector
from streamlit.testing.v1 import AppTest

from workspace_app.excel import xlsx_bytes
from workspace_app.google_scholar import fetcher, search
from workspace_app.jobs import JobManager


@pytest.mark.parametrize("mode,sort,filename,sheets", [
    ("profile", "citations", "profile_page", ["Artikel", "Profil", "Sitasi per Tahun"]),
    ("search", "relevance", "search_page", ["Hasil", "Pencarian"]),
])
def test_saved_scholar_job(tmp_path, monkeypatch, mode, sort, filename, sheets):
    page = Selector((Path(__file__).parent / "fixtures" / "scholar" / (filename + ".html")).read_text())
    monkeypatch.setattr(fetcher, "FetcherSession", lambda **kwargs: nullcontext(object()))
    monkeypatch.setattr(search, "browser_session", lambda *args, **kwargs: nullcontext(object()))
    scraper = fetcher.ScholarScraper if mode == "profile" else search.ScholarSearcher
    monkeypatch.setattr(scraper, "_get", lambda *args: page)
    manager = JobManager(tmp_path)
    params = dict(mode=mode, sort=sort, query="kukA0LcAAAAJ" if mode == "profile" else "learning", count=2)
    key = manager.start("google_scholar", params, project="Scholar")
    manager._thread.join(10)
    job = manager.get(key)
    assert job["status"] == "completed", job["message"]
    assert len(job["rows"]) == 2
    assert all(row["Judul"] for row in job["rows"])
    workbook = load_workbook(BytesIO(xlsx_bytes(job["rows"], sheet_name=sheets[0], extra_sheets=job["metadata"])))
    assert workbook.sheetnames == sheets
    assert all(sheet.max_row > 1 for sheet in workbook)
    app = AppTest.from_string("from workspace_app.views import render_result\n"
                              f"render_result({job!r})").run()
    assert not app.exception
    assert len(app.get("download_button")) == 3
    assert len(app.tabs) == 3


def test_later_page_error_keeps_profile_and_citations(tmp_path, monkeypatch):
    from workspace_app.google_scholar.errors import AccessBlockedError

    html = (Path(__file__).parent / "fixtures/scholar/profile_page.html").read_text()
    pages = iter([Selector(html)])
    def get_page(*args):
        page = next(pages, None)
        if page is None:
            raise AccessBlockedError("HTTP 429")
        return page

    monkeypatch.setattr(fetcher, "FetcherSession", lambda **kwargs: nullcontext(object()))
    monkeypatch.setattr(fetcher, "page_has_more", lambda page: True)
    monkeypatch.setattr(fetcher.ScholarScraper, "_get", get_page)
    manager = JobManager(tmp_path)
    key = manager.start("google_scholar", dict(mode="profile", sort="citations",
                        query="kukA0LcAAAAJ", count=200), project="Parsial")
    manager._thread.join(10)
    job = manager.get(key)
    assert job["status"] == "partial"
    assert job["message"] == "HTTP 429"
    assert job["rows"] and job["metadata"]["Profil"] and job["metadata"]["Sitasi per Tahun"]


@pytest.mark.parametrize("failed_status,expected_message", [(502, "HTTP 502"), (200, "tidak dapat dibaca")])
def test_search_failure_on_later_page_keeps_saved_results(tmp_path, monkeypatch, failed_status, expected_message):
    from types import SimpleNamespace

    fixture = (Path(__file__).parent / "fixtures/scholar/search_page.html").read_text()
    failed = SimpleNamespace(status=failed_status, url="", html_content="<html>Bad Gateway</html>",
                             css=lambda _: [])
    pages = iter([Selector(fixture), failed])
    session = SimpleNamespace(get=lambda _: next(pages))
    monkeypatch.setattr(search, "FetcherSession", lambda **kwargs: nullcontext(session))
    monkeypatch.setattr(search, "search_has_more", lambda page: True)
    monkeypatch.setattr(search.ScholarSearcher, "_page_wait", lambda self: 0)
    manager = JobManager(tmp_path)
    job_id = manager.start("google_scholar", dict(mode="search", sort="relevance",
                           query="learning", count=20, engine="http"), project="Parsial")
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "partial"
    assert expected_message in job["message"]
    assert len(job["rows"]) == 3


def test_search_explicit_no_results_page_is_successful(tmp_path, monkeypatch):
    page = Selector("<html><body>Your search did not match any articles.</body></html>")
    session = type("Session", (), {"get": lambda self, url: page})()
    monkeypatch.setattr(search, "FetcherSession", lambda **kwargs: nullcontext(session))
    manager = JobManager(tmp_path)
    job_id = manager.start("google_scholar", dict(mode="search", sort="relevance",
                           query="learning", count=20, engine="http"), project="Kosong")
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "completed", job["message"]
    assert job["rows"] == []
