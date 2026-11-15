from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import sources
from workspace_app.jobs import JobManager
from workspace_app.views import csv_bytes


def test_checkpoint_stop_and_job_exclusion(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    saved = Event()

    def execute(source, params, profile, stop, log, checkpoint):
        assert profile == tmp_path / "profiles" / source
        log("fixture")
        checkpoint([{"id": 1, "_internal": "ignored"}])
        saved.set()
        assert stop.wait(5)
        return [{"id": 1}, {"id": 2}], {}, None

    monkeypatch.setattr(sources, "execute", execute)
    job = manager.start("instagram", {"query": "#example"})
    try:
        assert saved.wait(5)
        assert manager.get(job)["rows"] == [{"id": 1}]
        assert manager.snapshot()[0]["id"] == job
        with pytest.raises(RuntimeError, match="pekerjaan aktif"):
            manager.start("news", {})
        with pytest.raises(RuntimeError, match="pekerjaan aktif"):
            manager.login("instagram")
        with pytest.raises(RuntimeError, match="folder riwayat"):
            JobManager(tmp_path)
        manager.delete()
        assert manager.get(job)["status"] == "running"
    finally:
        manager.stop()
        manager._thread.join(5)
    result = manager.get(job)
    assert result["status"] == "stopped"
    assert result["rows"] == [{"id": 1}, {"id": 2}]
    assert result["params"] == {"query": "#example"}
    manager.delete(job)
    assert manager.get(job) is None


def test_recover_interrupted_checkpoint(tmp_path):
    manager = JobManager(tmp_path)
    with manager.db() as db:
        db.execute(
            "INSERT INTO jobs(id, source, status, started, params, rows, count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fixture", "news", "stopping", "2026-01-01", '{"keyword":"test"}', '[{"title":"A"}]', 1),
        )
    manager._instance.close()
    restarted = JobManager(tmp_path)
    result = restarted.get("fixture")
    assert result["status"] == "interrupted"
    assert result["rows"] == [{"title": "A"}]
    assert result["count"] == 1
    assert csv_bytes(result["rows"]).decode("utf-8-sig").splitlines() == ["title", "A"]


def test_all_source_forms_and_module_loading(tmp_path):
    code = """
import streamlit as st
from workspace_app.views import source_page
from workspace_app.jobs import JobManager

@st.cache_resource
def manager():
    return JobManager(PATH)
source_page(manager(), SOURCE)
"""
    for source in sources.LABELS:
        scraper = sources.load(source)
        assert callable(getattr(scraper, sources.FUNCTIONS[source]))
        app = AppTest.from_string(
            f"PATH = {str(tmp_path / source)!r}\nSOURCE = {source!r}\n" + code
        ).run()
        assert not app.exception
        assert app.title[0].value == sources.LABELS[source]


def test_partial_result_survives_backend_error(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)

    def execute(*args):
        args[-1]([{"title": "Saved"}])
        raise RuntimeError("Fixture failure")

    monkeypatch.setattr(sources, "execute", execute)
    job_id = manager.start("news", {})
    manager._thread.join(5)
    result = manager.get(job_id)
    assert result["status"] == "partial"
    assert result["rows"] == [{"title": "Saved"}]
    assert result["message"] == "Fixture failure"


def test_job_logs_survive_restart_and_follow_job_deletion(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)

    def execute(source, params, profile, stop, log, checkpoint):
        log(params["keyword"])
        return [{"text": params["keyword"]}], {}, None

    monkeypatch.setattr(sources, "execute", execute)
    first = manager.start("news", {"keyword": "Pertama"})
    manager._thread.join(5)
    second = manager.start("news", {"keyword": "Kedua"})
    manager._thread.join(5)
    manager._instance.close()

    restarted = JobManager(tmp_path)
    assert restarted.logs(first) == ["Pertama"]
    assert restarted.logs(second) == ["Kedua"]
    restarted.delete(first)
    assert restarted.logs(first) == []
    assert restarted.logs(second) == ["Kedua"]


def test_stop_also_closes_login_job(tmp_path, monkeypatch):
    from types import SimpleNamespace

    manager = JobManager(tmp_path)
    started = Event()

    def login(*, user_data_dir, real_chrome, stop_event):
        started.set()
        assert stop_event.wait(5)
        return {"ok": False, "message": "interrupted"}

    monkeypatch.setattr(sources, "load", lambda source: SimpleNamespace(login=login))
    manager.login("x")
    assert started.wait(5)
    manager.stop()
    manager._thread.join(5)
    active, login_status = manager.snapshot()
    assert active is None
    assert login_status["x"] == "Login dihentikan."


def test_xlsx_preserves_numeric_data_and_writes_formula_like_strings_as_text():
    from io import BytesIO
    from xml.etree import ElementTree
    from zipfile import ZipFile

    from workspace_app.excel import xlsx_bytes

    rows = [{"angka": 42, "id": 123456789012345678, "komentar": "=1+1"},
            {"komentar": "https://example.com", "opsional": True}]
    with ZipFile(BytesIO(xlsx_bytes(rows))) as archive:
        assert archive.testzip() is None
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cells = {cell.attrib["r"]: cell for cell in root.findall(".//x:c", ns)}
    assert cells["A2"].findtext("x:v", namespaces=ns) == "42"
    assert cells["B2"].findtext("x:is/x:t", namespaces=ns) == "123456789012345678"
    assert cells["C2"].findtext("x:is/x:t", namespaces=ns) == "=1+1"
    assert cells["D3"].attrib["t"] == "b"
    assert root.find(".//x:f", ns) is None
    assert root.find("x:autoFilter", ns).attrib["ref"] == "A1:D3"


@pytest.mark.parametrize(("source", "documents", "expected"), [
    ("scholar", [("hasil.html", b'''
        <div id="gs_res_ccl_mid"><div class="gs_r gs_or">
        <h3 class="gs_rt"><a href="https://example.org/paper">Artikel A</a></h3>
        <div class="gs_a">Penulis - Jurnal, 2024 - example.org</div>
        </div></div>''')], "Artikel A"),
    ("openalex", [("works.csv", b"title,id,publication_year\nArtikel B,W123,2025\n"),
                  ("repeated.csv", b"title,id,publication_year\nArtikel B,W123,2025\n")], "Artikel B"),
])
def test_publication_import_is_saved_as_a_workspace_job(tmp_path, source, documents, expected):
    manager = JobManager(tmp_path)
    params = {"mode": "Kata kunci", "target": "artikel", "url": "", "limit": 100,
              "action": "import", "files": [name for name, _ in documents]}
    job_id = manager.start(source, params, documents=documents)
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "completed", job["message"]
    assert [row["judul"] for row in job["rows"]] == [expected]
    assert job["params"] == params
    assert any("Membaca" in line for line in manager.logs(job_id))


def test_scholar_preview_retains_partial_results_and_exports_profile_metrics(tmp_path, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile

    from workspace_app.excel import xlsx_bytes

    parser = sources.load("scholar")
    monkeypatch.setattr(parser, "fetch_profile", lambda url: b'''
        <table><tbody id="gsc_a_b"><tr class="gsc_a_tr">
        <td class="gsc_a_t"><a class="gsc_a_at">Publikasi A</a></td></tr></tbody></table>
        <table id="gsc_rsb_st"><tbody><tr><td class="gsc_rsb_std">42</td></tr></tbody></table>
        <button id="gsc_bpf_more">Show more</button>
    ''')
    manager = JobManager(tmp_path)
    url = "https://scholar.google.com/citations?user=example&hl=en"
    job_id = manager.start("scholar", {"mode": "Profil penulis", "target": url, "url": url,
                                       "limit": None, "action": "preview", "files": []})
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "partial", job["message"]
    assert job["rows"][0]["judul"] == "Publikasi A"
    metrics = job["metadata"]["profile_metrics"]
    assert metrics[0]["semua"] == 42
    with ZipFile(BytesIO(xlsx_bytes(job["rows"], profile_metrics=metrics))) as archive:
        assert b"Metrik profil" in archive.read("xl/workbook.xml")
        assert b"total_sitasi" in archive.read("xl/worksheets/sheet2.xml")


def test_openalex_preview_exports_metrics_as_result_rows(tmp_path, monkeypatch):
    parser = sources.load("openalex")
    monkeypatch.setattr(parser, "fetch_profile", lambda identifier: {"id_openalex": identifier, "h_index": 12})
    manager = JobManager(tmp_path)
    job_id = manager.start("openalex", {"mode": "Profil penulis", "target": "A123",
                                        "url": "https://openalex.org/authors/A123",
                                        "limit": None, "action": "preview", "files": []})
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "completed", job["message"]
    assert job["rows"] == [{"id_openalex": "A123", "h_index": 12}]
