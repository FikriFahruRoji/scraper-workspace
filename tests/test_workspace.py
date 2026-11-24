from pathlib import Path
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


def test_all_source_forms_and_module_loading(tmp_path, monkeypatch):
    from workspace_app import oecd_view  # the OECD form lists datasets from the network: use a fixture instead
    monkeypatch.setattr(oecd_view, "_catalogue", lambda: [
        {"flow": "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "name": "Consumer price indices"}])
    monkeypatch.setattr(oecd_view, "_structure", lambda flow: {"observations": 10, "dims": [
        {"id": "REF_AREA", "name": "Reference area", "codes": [("IDN", "Indonesia"), ("FRA", "France")]},
        {"id": "FREQ", "name": "Frequency", "codes": [("A", "Annual"), ("M", "Monthly")]}]})
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


def test_login_button_tracks_detected_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    manager = JobManager(tmp_path)
    monkeypatch.setattr(sources, "load", lambda source: SimpleNamespace(login=lambda **kwargs: {"ok": True}))
    app = AppTest.from_string("""
from streamlit import session_state
from workspace_app.views import source_page
source_page(session_state["manager"], "x")
""")
    app.session_state["manager"] = manager
    app.run()
    assert not app.button(key="login-x").disabled

    app.button(key="login-x").click().run()
    manager._thread.join(5)
    app.run()
    assert not app.exception
    assert app.button(key="login-x").disabled

    manager.start("x", {}, work=lambda *args: ([], {}, "not_logged_in"))
    manager._thread.join(5)
    app.run()
    assert not app.button(key="login-x").disabled


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


def test_default_project_names_source_subject_and_date():
    from datetime import datetime
    from workspace_app.jobs import default_project
    when = datetime(2026, 11, 18)
    url = "https://www.google.com/maps/place/Kopi+Kenangan+Senayan/@-6.2,106.8,17z"
    assert default_project("gmaps", {"url_input": url}, when) == "Google Maps Review · Kopi Kenangan Senayan · 18-11-2026"
    assert default_project("news", {"keyword": "  banjir   jakarta "}, when) == "Berita · banjir jakarta · 18-11-2026"
    assert default_project("saham", {}, when) == "Saham · 18-11-2026"


def test_projects_group_rename_and_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "execute", lambda *args, **kwargs: ([{"id": 1}], {}, None))
    manager = JobManager(tmp_path)
    first = manager.start("news", {"keyword": "banjir"}, project="  Riset   Banjir ")
    manager._thread.join(5)
    second = manager.start("instagram", {"query": "#banjir"}, project="Riset Banjir")
    manager._thread.join(5)
    third = manager.start("news", {"keyword": "pemilu"})
    manager._thread.join(5)
    assert manager.get(first)["project"] == "Riset Banjir"
    assert manager.get(third)["project"].startswith("Berita · pemilu · ")
    assert set(manager.projects()) == {"Riset Banjir", manager.get(third)["project"]}
    assert {job["id"] for job in manager.list_jobs(project="Riset Banjir")} == {first, second}
    assert [job["id"] for job in manager.list_jobs("news", project="Riset Banjir")] == [first]
    manager.rename(third, "Pemilu 2029")
    assert manager.get(third)["project"] == "Pemilu 2029"
    with pytest.raises(ValueError):
        manager.rename(third, "   ")


def test_existing_history_gets_project_names(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "history.sqlite3")
    db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
        started TEXT NOT NULL, finished TEXT, params TEXT NOT NULL, rows TEXT NOT NULL DEFAULT '[]',
        count INTEGER NOT NULL DEFAULT 0, metadata TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT '')""")
    db.execute("INSERT INTO jobs (id, source, status, started, params) VALUES "
               "('old', 'youtube', 'completed', '2026-01-05T03:00:00+00:00', '{\"url_input\": \"abc123\"}')")
    db.commit()
    db.close()
    manager = JobManager(tmp_path)
    assert manager.get("old")["project"] == "YouTube · abc123 · 05-01-2026"


def _seeded_manager(tmp_path, monkeypatch):
    batches = iter([
        [{"judul": f"Berita {n}", "isi": "x" * 300} for n in range(25)],
        [{"nama": "Kopi"}] * 3,
        [{"judul": "Lain"}] * 2,
    ])
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (next(batches), {}, None))
    manager = JobManager(tmp_path)
    for source, params, project in [("news", {"keyword": "banjir"}, "Riset Banjir"),
                                    ("gmaps", {"url_input": "kopi"}, "Riset Banjir"),
                                    ("news", {"keyword": "pemilu"}, "Pemilu")]:
        manager.start(source, params, project=project)
        manager._thread.join(5)
    return manager


def test_inventory_summarises_and_filters_by_project_and_source(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    sources_present, projects = manager.inventory_options()
    assert sources_present == ["news", "gmaps"]
    assert set(projects) == {"Riset Banjir", "Pemilu"}
    assert manager.inventory_options(["gmaps"])[1] == ["Riset Banjir"]
    summary = {item["source"]: item for item in manager.inventory()}
    assert (summary["news"]["jobs"], summary["news"]["rows"], summary["news"]["projects"]) == (2, 27, 2)
    assert summary["gmaps"]["rows"] == 3
    assert [i["source"] for i in manager.inventory(["Riset Banjir"])] == ["news", "gmaps"]
    assert manager.inventory(["Riset Banjir"], ["news"])[0]["rows"] == 25
    assert manager.inventory(["Tidak ada"]) == []


def test_inventory_skips_running_empty_and_unknown_source_jobs(tmp_path, monkeypatch):
    import sqlite3
    manager = _seeded_manager(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "history.sqlite3") as db:
        for job_id, source, status, count, project in [("a", "news", "running", 9, "Berjalan"),
                                                       ("b", "news", "failed", 0, "Kosong"),
                                                       ("c", "scholar", "completed", 5, "Yatim")]:
            db.execute("INSERT INTO jobs (id, source, status, started, params, count, project) "
                       "VALUES (?, ?, ?, '2026-11-18T00:00:00+00:00', '{}', ?, ?)",
                       (job_id, source, status, count, project))
    assert not {"Berjalan", "Kosong", "Yatim"} & set(manager.inventory_options()[1])
    assert manager.inventory(sources_=["scholar"]) == []
    assert sum(item["rows"] for item in manager.inventory()) == 30
    assert all(job["source"] != "scholar" for job in manager.list_jobs())
    assert "Yatim" not in manager.projects()


def test_sample_rows_returns_only_the_requested_head_with_job_tags(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    rows = manager.sample_rows("news", limit=10)
    assert len(rows) == 10
    assert rows[0]["Proyek"] == "Pemilu"  # newest job first
    assert {"Proyek", "Pekerjaan", "Dimulai", "Status pekerjaan"} <= set(rows[0])
    spread = manager.sample_rows("news", limit=4)
    assert [row["Proyek"] for row in spread] == ["Pemilu"] * 2 + ["Riset Banjir"] * 2
    assert {row["Proyek"] for row in manager.sample_rows("news", ["Riset Banjir"], 10)} == {"Riset Banjir"}




def test_start_button_requires_a_project_name(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "execute", lambda *a, **k: ([{"id": 1}], {}, None))
    script = tmp_path / "form.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import streamlit as st\n"
        "from workspace_app.views import scraper_form\n"
        "scraper_form(st.session_state['manager'], 'youtube', None, {})\n"
    )
    at = AppTest.from_file(str(script), default_timeout=30)
    manager = JobManager(tmp_path / "data")
    manager.start("youtube", {"url_input": "abc"}, project="Riset Video")
    manager._thread.join(5)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    start = next(b for b in at.button if b.label == "Mulai Ambil Data")
    assert start.disabled
    assert not any("Otomatis" in str(getattr(box, "placeholder", "")) for box in at.selectbox)
    at.selectbox(key="project-youtube").select("Riset Video").run()
    start = next(b for b in at.button if b.label == "Mulai Ambil Data")
    assert not start.disabled


def _page_script(tmp_path, body):
    script = tmp_path / "page.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import streamlit as st\n" + body
    )
    return str(script)


def test_preprocessing_job_is_saved_apart_from_scraping_history(tmp_path, monkeypatch):
    from workspace_app import preprocess as pp
    manager = _seeded_manager(tmp_path, monkeypatch)
    news = [job["id"] for job in manager.list_jobs("news")]
    preview = manager.input_rows(news, limit=3)
    # The newest job has 2 rows, so the third preview row comes from the older one.
    assert [row["pekerjaan_asal"] for row in preview] == [news[0][:8]] * 2 + [news[1][:8]]
    rows = manager.input_rows(news)
    assert len(rows) == 27
    config = {**pp.FULL, **pp.DEFAULT_LISTS, "stemming": False}
    with pytest.raises(ValueError, match="nama proyek"):
        manager.start_preprocessing({}, rows, ["judul"], config, pp.build_resources(config), project=" ")
    job_id = manager.start_preprocessing({"kolom": ["judul"]}, rows, ["judul"], config,
                                         pp.build_resources(config), project="Riset Banjir")
    manager._thread.join(10)
    job = manager.get(job_id)
    assert job["status"] == "completed", job["message"]
    assert job["rows"][0]["teks_bersih"] == "berita"
    assert job["metadata"]["laporan"]["baris_masuk"] == 27
    assert job_id not in {item["id"] for item in manager.list_jobs()}
    assert [item["id"] for item in manager.list_jobs("preprocessing")] == [job_id]
    assert "preprocessing" not in {item["source"] for item in manager.inventory()}
    assert manager.projects("preprocessing") == ["Riset Banjir"]
    manager.delete(sources_=["preprocessing"])
    assert manager.list_jobs("preprocessing") == [] and len(manager.list_jobs()) == 3


def test_preprocessing_page_previews_and_switches_presets(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.preprocess_view import preprocessing_page\n"
                  "preprocessing_page(st.session_state['manager'])\n"), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.session_state["pp-stemming"] is False
    at.radio(key="pp-preset").set_value("Lengkap (analisis kata)").run()
    assert not at.exception
    assert at.session_state["pp-stemming"] is True and at.session_state["pp-sw-project"] is True
    assert at.session_state["pp-sw-sastrawi"] is False
    start = next(button for button in at.button if button.label == "Jalankan preprocessing")
    assert start.disabled  # saving needs a project name
    at.toggle(key="pp-save").set_value(False).run()
    start = next(button for button in at.button if button.label == "Jalankan preprocessing")
    assert not start.disabled
    start.click().run()
    assert not at.exception
    result = at.session_state["pp-result"]
    assert result["report"]["baris_masuk"] == 2 and result["params"]["masukan"]["sumber"] == "news"


def test_preprocessing_dataset_opens_report_and_downloads(tmp_path, monkeypatch):
    from workspace_app import preprocess as pp
    manager = _seeded_manager(tmp_path, monkeypatch)
    config = {**pp.LIGHT, **pp.DEFAULT_LISTS}
    params = {"masukan": {"jenis": "berkas", "berkas": "data.csv"}, "kolom": ["t"], "konfigurasi": config}
    manager.start_preprocessing(params, [{"t": "Halo bgt"}], ["t"], config, pp.build_resources(config), project="Uji")
    manager._thread.join(10)
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.studio import preprocessing_dataset_page\n"
                  "preprocessing_dataset_page(st.session_state['manager'])\n"), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dataset Preprocessing"
    next(button for button in at.button if button.label == "Buka").click().run()
    assert not at.exception
    assert {b.label for b in at.get("download_button")} >= {"Unduh Excel", "Unduh CSV", "Unduh JSON"}
    assert any("data.csv" in caption.value for caption in at.caption)
    assert [metric.value for metric in at.metric][:2] == ["1", "1"]


def test_scraping_catalogue_and_dataset_list_render(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.studio import scraping_page\nscraping_page(st.session_state['manager'])\n"),
        default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.title[0].value == "Lakukan Scraping"
    assert sum(b.label == "Kumpulkan Data" for b in at.button) == len(sources.LABELS)
    at.text_input[0].input("youtube").run()
    assert [b.key for b in at.button if b.label == "Kumpulkan Data"] == ["open-youtube"]

    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.studio import dataset_page\ndataset_page(st.session_state['manager'])\n"),
        default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dataset"
    assert sum(b.label == "Buka" for b in at.button) == 3
    assert any("Menampilkan 1-3 dari 3" in caption.value for caption in at.caption)



def test_indonesian_number_format_and_friendly_errors():
    from workspace_app import ui
    assert ui.fmt(102424) == "102.424"
    assert ui.friendly("no_posts") == "Tidak ada post yang ditemukan untuk input ini."
    assert ui.duration("2026-01-01T00:00:00+00:00", "2026-01-01T00:03:23+00:00") == "3 menit 23 detik"


def test_method_record_reads_like_a_methods_section():
    from workspace_app.views import method_rows, method_text
    job = {"source": "x", "status": "completed", "count": 1240,
           "started": "2026-11-19T03:51:00+00:00", "finished": "2026-11-19T03:51:31+00:00",
           "params": {"mode": "search", "keyword": "gojek OR grab", "start_date": "2026-10-21",
                      "end_date": "2026-11-20", "sort": "latest", "max_minutes": 10}}
    rows = dict(method_rows(job))
    assert rows["Sumber"] == "X / Twitter · Pencarian"
    assert rows["Kata kunci"] == "gojek OR grab"
    assert rows["Rentang tanggal"] == "21 Okt 2026 – 20 Nov 2026"
    assert rows["Urutan"] == "Terbaru dulu"
    assert rows["Hasil"] == "1.240 baris · Selesai"
    text = method_text(job)
    assert text.startswith("Data dikumpulkan dari X / Twitter · Pencarian menggunakan nOCturnal Scraper")
    assert "kata kunci: gojek OR grab" in text and "durasi 31 detik" in text


def test_rerun_prefills_the_source_form(tmp_path):
    script = _page_script(tmp_path, """
from workspace_app.views import scraper_form, seed_form
from workspace_app.jobs import JobManager
if "seeded" not in st.session_state:
    st.session_state["seeded"] = True
    seed_form("x", {"mode": "profile", "username": "kompascom", "start_date": "2026-01-01",
                    "end_date": "2026-01-31", "sort": "top", "max_minutes": 5}, "Riset Ojol")
scraper_form(JobManager(PATH), "x", None, {})
""".replace("PATH", repr(str(tmp_path / "data"))))
    at = AppTest.from_file(script, default_timeout=30).run()
    assert not at.exception
    assert at.text_input(key="f-x-username").value == "kompascom"
    assert at.radio(key="mode-x").value == "profile"
    assert at.radio(key="f-x-sort").value == "top"
    assert str(at.date_input(key="f-x-start").value) == "2026-01-01"
    assert not next(b for b in at.button if b.label == "Mulai Ambil Data").disabled


def test_failed_job_explains_and_suggests_next_steps():
    from workspace_app import ui
    from workspace_app.views import recovery_hints
    job = {"source": "x", "status": "failed", "count": 0, "message": "Tidak ada tweet yang cocok ditemukan.",
           "params": {"mode": "search", "keyword": "gojek", "start_date": "2026-11-01",
                      "end_date": "2026-11-02", "sort": "latest"}}
    hints = recovery_hints(job)
    assert hints[0].startswith("Pastikan kamu sudah login")
    assert "Lebarkan rentang tanggal." in hints and "Coba urutan **Teratas**." in hints
    assert recovery_hints({**job, "status": "completed", "count": 5}) == []
    assert recovery_hints({**job, "status": "interrupted"}) == ["Jalankan ulang untuk mengambil lagi dari awal. Dataset ini tetap tersimpan terpisah."]
    assert ui.short_datetime("2026-11-19T02:58:00+00:00").startswith("19 Nov 2026")


def test_merge_combines_datasets_and_tags_origin(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    first, second, third = [job["id"] for job in manager.list_jobs(limit=10)]  # newest first
    manager.start_merge({"dataset": []}, [first, second, third], project="Gabungan Uji", drop_duplicates=True)
    manager._thread.join(5)
    job = manager.list_jobs("gabungan")[0]
    assert job["status"] == "completed"
    rows = manager.get(job["id"])["rows"]
    # 25 news + 3 identical gmaps rows (deduplicated to 1) + 2 identical news rows (deduplicated to 1).
    assert len(rows) == 25 + 1 + 1
    assert set(rows[0]) >= {"sumber_data", "pekerjaan_asal"}
    assert {row["sumber_data"] for row in rows} == {"Berita", "Google Maps Review"}
    assert manager.get(job["id"])["metadata"]["duplikat_dibuang"] == 3
    assert "gabungan" in manager.inventory_options()[0]
    with __import__("pytest").raises(ValueError):
        manager.start_merge({}, [first], project="X")


def test_analysis_functions_on_small_text_data():
    import pandas as pd
    from workspace_app import analysis as an
    docs = ["banjir jakarta parah hari ini", "banjir jakarta surut pagi", "gubernur tinjau banjir jakarta",
            "harga bensin naik hari ini", "harga bensin turun besok", "bensin naik lagi harga mahal"] * 3
    assert an.word_counts(docs, 3)["Kata"].tolist()[0] in {"banjir", "jakarta", "harga", "bensin", "hari"}
    assert an.bigram_counts(docs, 1)["Pasangan kata"].iloc[0] in {"banjir jakarta", "harga bensin", "bensin naik", "hari ini"}
    table, assignment = an.topics(docs, 2, 4)
    assert len(table) == 2 and len(assignment) == len(docs) and table["Dokumen"].sum() == len(docs)
    nodes, edges = an.word_network(docs, 8, 2)
    assert not nodes.empty and {"a", "b", "weight"} <= set(edges)
    frame = pd.DataFrame({"waktu": ["2026-11-19 09:58:51 +0700", "2026-11-19 22:00:00 +0700", "2026-11-20 01:00:00 +0700"],
                          "tanggal": ["2026-11-19", "2026-11-19", "2026-11-20"], "angka": [1, 2, 3]})
    assert an.date_columns(frame)[0] in {"waktu", "tanggal"}
    volume, skipped = an.volume_over_time(frame, "waktu", "D")
    assert volume["Jumlah"].tolist() == [2, 1] and skipped == 0  # WIB day boundaries, not UTC
    with __import__("pytest").raises(ValueError):
        an.topics(docs[:3], 5)


def test_analysis_and_merge_pages_render(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    for key in ("statistik", "frekuensi", "tren", "topik", "jaringan"):
        at = AppTest.from_file(_page_script(
            tmp_path, f"from workspace_app.analysis_view import page_for\npage_for(st.session_state['manager'], {key!r})()\n"),
            default_timeout=60)
        at.session_state["manager"] = manager
        at.run()
        assert not at.exception, key
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.merge_view import merge_page\nmerge_page(st.session_state['manager'])\n"),
        default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.title[0].value == "Gabungkan Data"


def test_running_job_notice_sits_under_the_start_button(tmp_path, monkeypatch):
    manager = JobManager(tmp_path / "data")
    monkeypatch.setattr(manager, "snapshot", lambda: (
        {"source": "gmaps", "id": "abc", "stopping": False, "login": False}, {}))
    monkeypatch.setattr(manager, "saved_count", lambda job_id: 3600)
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.views import scraper_form\n"
                  "scraper_form(st.session_state['manager'], 'youtube', "
                  "st.session_state['manager'].snapshot()[0], {})\n"), default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert any("mengambil data · 3.600 baris tersimpan" in info.value for info in at.info)
    labels = [b.label for b in at.button]
    assert labels.index("Mulai Ambil Data") < labels.index("Hentikan")


def test_progress_is_read_from_the_newest_log_line():
    from workspace_app.process_view import progress_from_logs
    logs = ["Memproses 20.000 baris dari kolom komentar.", "2.000 dari 20.000 baris diproses.",
            "4.000 dari 20.000 baris diproses."]
    assert progress_from_logs(logs) == (4000, 20000)
    assert progress_from_logs(["Menyiapkan"]) is None


def test_process_page_shows_log_while_running_and_a_summary_afterwards(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    job_id = manager.list_jobs(limit=1)[0]["id"]
    manager._log(job_id, "4.000 dari 20.000 baris diproses.")
    script = _page_script(tmp_path, "from workspace_app.process_view import process_page\n"
                                    "from workspace_app import ui\n"
                                    "ui.PAGES.update({'pp-dataset': None, 'dataset': None, 'preprocessing': None})\n"
                                    "st.page_link = lambda *a, **k: None\n"
                                    "process_page(st.session_state['manager'])\n")
    idle = AppTest.from_file(script, default_timeout=30)
    idle.session_state["manager"] = manager
    idle.run()
    assert not idle.exception
    assert any("Tidak ada proses yang berjalan" in info.value for info in idle.info)

    monkeypatch.setattr(manager, "snapshot", lambda: (
        {"source": "preprocessing", "id": job_id, "stopping": False, "login": False}, {}))
    running = AppTest.from_file(script, default_timeout=30)
    running.session_state["manager"] = manager
    running.run()
    assert not running.exception
    assert any("4.000 dari 20.000 baris diproses." in code.value for code in running.code)
    assert any(b.label == "Hentikan" for b in running.button)


def test_process_page_handles_login_without_a_job(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    active = {"source": "x", "id": None, "stopping": False, "login": True}
    monkeypatch.setattr(manager, "snapshot", lambda: (active, {}))
    monkeypatch.setattr(manager, "stop", lambda: active.update(stopping=True))
    app = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.process_view import process_page\n"
                  "process_page(st.session_state['manager'])\n"))
    app.session_state["manager"] = manager
    app.run()
    assert not app.exception
    assert any("X / Twitter" in m.value for m in app.markdown)
    app.button(key="process-stop").click().run()
    assert not app.exception
    assert app.button(key="process-stop").disabled


def test_preprocessing_dataset_page_lists_only_preprocessing_jobs(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)
    manager.start_preprocessing({"masukan": {}, "kolom": ["judul"], "konfigurasi": {}}, [{"judul": "Banjir Jakarta"}],
                                ["judul"], __import__("workspace_app.preprocess", fromlist=["x"]).LIGHT,
                                __import__("workspace_app.preprocess", fromlist=["x"]).build_resources(
                                    {"stopword_sources": [], "keep_negation": True}), project="Bersih")
    manager._thread.join(10)
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.studio import preprocessing_dataset_page\n"
                  "preprocessing_dataset_page(st.session_state['manager'])\n"), default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dataset Preprocessing"
    assert sum(b.label == "Buka" for b in at.button) == 1


def test_legacy_publication_datasets_open_and_export(tmp_path):
    import json

    manager = JobManager(tmp_path)
    rows = [{"judul": "Artikel lama", "tahun": 2024, "doi": "10.123/abc"}]
    with manager.db() as db:
        for source in ("scholar", "openalex", "google_scholar"):
            db.execute(
                "INSERT INTO jobs (id, source, status, started, params, rows, count, project) "
                "VALUES (?, ?, 'completed', '2026-11-18T00:00:00+00:00', '{}', ?, 1, ?)",
                (source, source, json.dumps(rows), f"Riset {source}"),
            )
    script = _page_script(
        tmp_path, "from workspace_app.studio import dataset_page\n"
                  "dataset_page(st.session_state['manager'])\n")
    for source in ("scholar", "openalex"):
        app = AppTest.from_file(script)
        app.session_state["manager"] = manager
        app.run()
        assert {b.key for b in app.button} >= {f"open-job-{s}" for s in ("scholar", "openalex", "google_scholar")}
        app.button(key=f"open-job-{source}").click().run()
        assert not app.exception
        assert len(app.get("download_button")) == 3
        assert app.dataframe[0].value.to_dict("records") == rows
        assert all("Jalankan ulang" not in button.label for button in app.button)
        assert csv_bytes(manager.get(source)["rows"]).decode("utf-8-sig").splitlines() == [
            "judul,tahun,doi", "Artikel lama,2024,10.123/abc"]
    assert [job["source"] for job in manager.list_jobs()] == ["google_scholar"]


def test_excel_text_and_original_columns_survive_persistence(tmp_path):
    from io import BytesIO

    from openpyxl import Workbook

    from workspace_app import preprocess as pp
    from workspace_app.analysis import cleaned_documents
    from workspace_app.preprocess_view import read_table

    workbook = Workbook()
    workbook.active.append(["caption", "_id", 2025])
    for index, value in enumerate(("NA", "NULL", "None", "n/a", None)):
        workbook.active.append([value, f"id-{index}", str(index)])
    buffer = BytesIO()
    workbook.save(buffer)
    records = read_table("upload.xlsx", buffer.getvalue())
    csv_records = read_table("upload.csv", b"caption,_id,2025\nNA,id-0,0\nNULL,id-1,1\nNone,id-2,2\nn/a,id-3,3\n,id-4,4\n")
    assert records == csv_records
    manager = JobManager(tmp_path)
    config = {**pp.LIGHT, **pp.DEFAULT_LISTS, "drop_empty": True}
    job_id = manager.start_preprocessing({}, records, ["caption"], config, pp.build_resources(config), project="Uji")
    manager._thread.join(5)
    job = manager.get(job_id)
    assert job["status"] == "completed", job["message"]
    assert [{k: row[k] for k in ("_id", "caption", "2025")} for row in job["rows"]] == csv_records[:-1]
    documents, skipped = cleaned_documents(job["rows"], ["teks_bersih"])
    assert len(documents) == 4 and skipped == 0


def test_merge_preserves_origin_and_preprocessing_discovers_later_columns(tmp_path):
    from workspace_app.preprocess_view import PREVIEW_ROWS

    manager = JobManager(tmp_path)

    def save(source, rows):
        job_id = manager.start(source, {}, project="Uji", work=lambda *_: (rows, {}, None))
        manager._thread.join(5)
        return job_id

    news = save("news", [{"title": "Artikel berita"}] * PREVIEW_ROWS)
    maps = save("gmaps", [{"ulasan": "ulasan lokasi"}])
    merged = manager.start_merge({}, [news, maps], project="Uji")
    manager._thread.join(5)
    assert all("ulasan" not in row for row in manager.input_rows([merged], PREVIEW_ROWS))
    assert {"title", "ulasan"} <= set(manager.input_text_columns([merged]))
    extra = save("news", [{"title": "Berita lain"}])
    combined = manager.start_merge({}, [merged, extra], project="Uji")
    manager._thread.join(5)
    assert {row["pekerjaan_asal"] for row in manager.get(combined)["rows"]} == {
        news[:8], maps[:8], extra[:8]}

    app = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.preprocess_view import preprocessing_page\n"
                  "preprocessing_page(st.session_state['manager'])\n"))
    app.session_state["manager"] = manager
    app.run()
    app.selectbox(key="pp-source").select("gabungan").run()
    assert not app.exception
    assert {"title", "ulasan"} <= set(app.multiselect(key="pp-columns-gabungan").value)
    app.toggle(key="pp-save").set_value(False).run()
    next(b for b in app.button if b.label == "Jalankan preprocessing").click().run()
    assert not app.exception
    assert any(row["teks_bersih"] == "ulasan lokasi" for row in app.session_state["pp-result"]["rows"])


def test_analysis_missing_text_unassigned_topics_and_monthly_trend():
    import pandas as pd

    from workspace_app import analysis as an

    frame = pd.DataFrame([{"teks_bersih": "kopi lokal"}, {"judul": "data mentah"}, {"teks_bersih": "nan"}])
    assert an.cleaned_documents(frame.to_dict("records"), ["teks_bersih"]) == (["kopi lokal", "nan"], 1)
    documents = ["kopi panen"] * 4 + ["kopi kopi panen"] + ["mesin diesel"] * 4 + ["mesin mesin diesel", "unik"]
    table, assignment = an.topics(documents, count=2)
    assert table["Dokumen"].tolist() == [5, 5, 1]
    assert table.iloc[assignment[-1]]["Topik"] == "Tanpa topik"
    assert {frozenset(terms.split(", ")) for terms in table["Kata utama"].iloc[:2]} == {
        frozenset({"kopi", "panen"}), frozenset({"mesin", "diesel"})}
    dates = pd.DataFrame({"waktu": ["2026-01-31T16:30Z", "2026-01-31T18:30Z"], "portal": ["A", "B"]})
    period = next(key for key, label in an.PERIODS.items() if label == "Bulanan")
    trend, skipped = an.volume_over_time(dates, "waktu", period)
    grouped, _ = an.volume_over_time(dates, "waktu", period, "portal")
    assert trend.index.month.tolist() == [1, 2]
    assert skipped == 0 and trend["Jumlah"].tolist() == grouped.sum(axis=1).tolist() == [1, 1]

    app = AppTest.from_string(
        "import pandas as pd\n"
        "from workspace_app.analysis_view import render_trend\n"
        f"render_trend(None, {{'project': 'Uji'}}, pd.DataFrame({dates.to_dict('list')!r}))\n"
    ).run()
    app.button_group(key="trend-period").set_value(period).run()
    app.selectbox(key="trend-group").select("portal").run()
    assert not app.exception
    assert len(app.get("download_button")) == 1


def test_merge_deduplicates_original_rows_against_tagged_merges(tmp_path):
    manager = JobManager(tmp_path)

    def save(source, title):
        job_id = manager.start(source, {}, project="Uji", work=lambda *_: ([{"judul": title}], {}, None))
        manager._thread.join(5)
        return job_id

    news, maps = save("news", "A"), save("gmaps", "B")
    merged = manager.start_merge({"tandai": True}, [news, maps], project="Gabungan")
    manager._thread.join(5)
    combined = manager.start_merge({"tandai": True}, [merged, news], project="Ulang", drop_duplicates=True)
    manager._thread.join(5)
    job = manager.get(combined)
    assert job["status"] == "completed", job["message"]
    assert job["rows"] == manager.get(merged)["rows"]
    assert job["metadata"]["duplikat_dibuang"] == 1


def test_analysis_selects_available_text_when_clean_column_is_null(tmp_path):
    manager = JobManager(tmp_path)
    manager.start("news", {}, project="Uji", work=lambda *_: (
        [{"caption": "kopi lokal pilihan petani", "teks_bersih": None}], {}, None))
    manager._thread.join(5)
    script = _page_script(tmp_path, "from workspace_app.analysis_view import page_for\n"
                                    "page_for(st.session_state['manager'], 'frekuensi')()\n")
    app = AppTest.from_file(script)
    app.session_state["manager"] = manager
    app.run()
    assert not app.exception
    assert app.multiselect(key="freq-columns").value == ["caption"]



IMF_SAMPLE = b"""<?xml version='1.0'?><message:StructureSpecificData xmlns:message="m" xmlns:ss="s"><message:DataSet>
<Group INDICATOR="NGDP_RPCH" SERIES_NAME="Gross domestic product (GDP), Constant prices, Percent change"/>
<Group COUNTRY="IDN" INDICATOR="NGDP_RPCH" LATEST_ACTUAL_ANNUAL_DATA="2025"/>
<Series COUNTRY="IDN" INDICATOR="NGDP_RPCH" FREQUENCY="A" SCALE="0">
<Obs TIME_PERIOD="2025" OBS_VALUE="5.1"/><Obs TIME_PERIOD="2026" OBS_VALUE="5.3"/><Obs TIME_PERIOD="2027"/></Series>
<Series COUNTRY="XYZ" INDICATOR="NGDP_RPCH" FREQUENCY="A" SCALE="0"><Obs TIME_PERIOD="FY2026/27" OBS_VALUE="2.0"/></Series>
</message:DataSet></message:StructureSpecificData>"""


def test_imf_xml_marks_projections_and_handles_fiscal_years():
    rows = sources.load("imf").parse(IMF_SAMPLE)
    by = {(row["kode_negara"], row["tahun"]): row for row in rows}
    assert by[("IDN", 2025)]["jenis"] == "aktual" and by[("IDN", 2026)]["jenis"] == "proyeksi"
    assert ("IDN", 2027) not in by  # an observation without a value is skipped
    assert by[("IDN", 2025)]["negara"] == "Indonesia" and by[("IDN", 2025)]["nilai"] == 5.1
    assert by[("IDN", 2025)]["indikator"].startswith("Gross domestic product")
    assert ("XYZ", 2026) in by  # 'FY2026/27' still yields a year


def test_worldbank_job_end_to_end_with_partial_failure(tmp_path, monkeypatch):
    import json
    module = sources.load("worldbank")

    def fake_get(url, **kwargs):
        if "BAD.CODE" in url:
            return json.dumps([{"message": [{"id": "120", "value": "The provided parameter value is not valid"}]}]).encode()
        record = lambda year, value: {"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"},
                                      "country": {"id": "ID", "value": "Indonesia"}, "countryiso3code": "IDN",
                                      "date": str(year), "value": value, "unit": ""}
        return json.dumps([{"page": 1, "pages": 1, "total": 3},
                           [record(2022, 275.0), record(2021, None), record(2020, 271.0)]]).encode()

    monkeypatch.setattr(module, "get", fake_get)
    manager = JobManager(tmp_path)
    params = {"countries": ["IDN"], "indicators": ["SP.POP.TOTL", "BAD.CODE"], "start_year": 2020, "end_year": 2022,
              "drop_empty": True, "max_minutes": 5}
    manager.start("worldbank", params, project="Penduduk")
    manager._thread.join(10)
    job = manager.get(manager.list_jobs(limit=1)[0]["id"])
    assert job["status"] == "partial"  # one indicator worked, one did not
    assert [row["tahun"] for row in job["rows"]] == [2020, 2022]  # empty year dropped, sorted ascending
    assert "BAD.CODE" in job["message"] and "tidak dikenali" in job["message"]


def test_open_data_form_validation_and_rerun_seed(tmp_path):
    from workspace_app.open_data_view import check, hints
    from workspace_app.views import validate
    base = {"countries": ["IDN"], "indicators": ["NGDP_RPCH"], "start_year": 2010, "end_year": 2020, "max_minutes": 5}
    validate("imf", base)
    for change, text in ((dict(countries=[]), "negara"), (dict(countries=["ID"]), "tiga huruf"),
                         (dict(indicators=[]), "indikator"), (dict(indicators=["a b!"]), "tidak valid"),
                         (dict(start_year=2030), "Tahun mulai")):
        with __import__("pytest").raises(ValueError, match=text):
            check({**base, **change})
    assert any("data.worldbank.org" in hint for hint in hints({"source": "worldbank"}))

    script = _page_script(tmp_path, """
from workspace_app.views import scraper_form, seed_form
from workspace_app.jobs import JobManager
if "seeded" not in st.session_state:
    st.session_state["seeded"] = True
    seed_form("worldbank", {"countries": ["IDN", "NOR"], "indicators": ["SP.POP.TOTL", "SP.POP.GROW"],
                            "start_year": 2015, "end_year": 2022, "drop_empty": False}, "Uji")
scraper_form(JobManager(PATH), "worldbank", None, {})
""".replace("PATH", repr(str(tmp_path / "data"))))
    at = AppTest.from_file(script, default_timeout=30).run()
    assert not at.exception
    assert at.multiselect(key="f-worldbank-countries").value == ["IDN"]
    assert at.text_input(key="f-worldbank-extra_countries").value == "NOR"
    assert at.text_input(key="f-worldbank-extra_indicators").value == "SP.POP.GROW"
    assert at.number_input(key="f-worldbank-start_year").value == 2015
    assert not at.checkbox(key="f-worldbank-drop_empty").value


def test_fx_market_turns_currency_pairs_into_yahoo_codes():
    module = sources.load("saham")
    normalize = module.normalize_symbol
    assert normalize("usd/idr", "FX") == "USDIDR=X"
    assert normalize("EUR IDR", "FX") == "EURIDR=X"
    assert normalize("idr", "FX") == "IDR=X"  # three letters means US dollar to that currency
    assert normalize("USDIDR=X", "FX") == "USDIDR=X"
    assert normalize("bbca", "IDX") == "BBCA.JK"  # other markets are unchanged
    with __import__("pytest").raises(module.ScraperError, match="tiga huruf"):
        normalize("dolar rupiah", "FX")


def quake(number, minute=0, mag=5.2, depth=10.0):
    return {"id": f"us{number}", "geometry": {"coordinates": [122.51, -3.88, depth]},
            "properties": {"time": 1795266541000 + minute * 60_000, "mag": mag, "magType": "mww",
                           "place": "10 km N of Kendari, Indonesia", "tsunami": 0, "felt": 12, "cdi": 3.1,
                           "mmi": None, "alert": "green", "status": "reviewed", "sig": 416, "net": "us",
                           "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us1"}}


def test_usgs_rows_are_numeric_wib_and_deduplicated(monkeypatch):
    import json
    from datetime import date
    module = sources.load("usgs")
    urls = []

    def fake(url, **kwargs):
        urls.append(url)
        return json.dumps({"features": [quake(1), quake(1), quake(2, minute=5, mag=4.6),
                                        {"id": "bad", "properties": {}, "geometry": {}}]}).encode()

    monkeypatch.setattr(module, "get", fake)
    result = module.collect_earthquakes("indonesia", date(2026, 11, 1), date(2026, 11, 20), 4.5)
    rows = result["rows"]
    assert len(rows) == 2 and result["error"] is None and len(urls) == 1  # duplicates and unreadable rows dropped
    assert "minmagnitude=4.5" in urls[0] and "minlatitude=-11.5" in urls[0] and "eventtype=earthquake" in urls[0]
    top = rows[0]
    assert top["id_kejadian"] == "us2" and top["waktu"].endswith("+07:00") and top["waktu_utc"].endswith("+00:00")
    assert (top["lintang"], top["bujur"], top["kedalaman_km"]) == (-3.88, 122.51, 10.0)  # south keeps its sign
    assert top["magnitudo"] == 4.6 and top["peringatan_pager"] == "hijau" and top["potensi_tsunami"] == "tidak"
    world = module.collect_earthquakes("dunia", date(2026, 11, 1), date(2026, 11, 2), 6)
    assert "minlatitude" not in urls[-1] and world["rows"]
    monkeypatch.setattr(module, "get", lambda url, **kwargs: b"{not json")
    broken = module.collect_earthquakes("indonesia", date(2026, 11, 1), date(2026, 11, 2))
    assert broken["rows"] == [] and broken["error"]


def test_usgs_splits_capped_windows_and_honours_count(monkeypatch):
    import json
    from datetime import date
    module = sources.load("usgs")
    monkeypatch.setattr(module, "CAP", 3)
    calls = []

    def fake(url, **kwargs):
        calls.append(url)
        window = [part.split("=")[1] for part in url.split("&") if part.startswith(("starttime", "endtime"))]
        number = len(calls)
        return json.dumps({"features": [quake(f"{number}{i}", minute=number * 10 + i) for i in range(3 if number == 1 else 2)]}).encode()

    monkeypatch.setattr(module, "get", fake)
    result = module.collect_earthquakes("indonesia", date(2026, 11, 1), date(2026, 11, 10))
    assert len(calls) == 3 and len(result["rows"]) == 4  # first window hit the cap, then two halves
    limited = module.collect_earthquakes("indonesia", date(2026, 11, 1), date(2026, 11, 10), count=1)
    assert len(limited["rows"]) == 1


def test_usgs_year_windows_cover_range_newest_first():
    module = sources.load("usgs")
    from datetime import date
    first, last = module.day_range(date(2020, 1, 1), date(2026, 11, 20))
    spans = list(module.yearly(first, last))
    assert spans[0][1] == last and spans[-1][0] == first
    assert all(later[1] == earlier[0] for earlier, later in zip(spans, spans[1:]))


def test_openmeteo_resolves_places_and_reports_the_ones_it_cannot(monkeypatch):
    import json
    module = sources.load("openmeteo")

    def fake_get(url, **kwargs):
        if "geocoding" in url:
            found = [{"name": "Kota Bandung", "admin1": "Jawa Barat", "country": "Indonesia",
                      "latitude": -6.92, "longitude": 107.6}] if "Bandung" in url else []
            return json.dumps({"results": found}).encode()
        if "latitude=99" in url:
            return json.dumps({"error": True, "reason": "Latitude must be in range"}).encode()
        return json.dumps({"latitude": -6.92, "longitude": 107.6, "elevation": 700.0, "daily": {
            "time": ["2026-11-01", "2026-11-02", "2026-11-03"], "temperature_2m_max": [30.1, None, 31.0],
            "precipitation_sum": [1.0, None, 0.0]}}).encode()

    monkeypatch.setattr(module, "get", fake_get)
    result = module.collect_weather(["Bandung", "Nowhere", "-6.2,106.8"], "history", "daily",
                                    ["temperature_2m_max", "precipitation_sum"], "2026-11-01", "2026-11-03")
    rows = result["rows"]
    assert len(rows) == 4  # two places found; the empty day is dropped for each
    assert rows[0]["lokasi"] == "Kota Bandung, Jawa Barat, Indonesia" and rows[0]["suhu_maks_c"] == 30.1
    assert rows[0]["tanggal"] == "2026-11-01" and rows[2]["lokasi"] == "-6.2,106.8"
    assert "Nowhere" in result["error"] and "tidak ditemukan" in result["error"]
    kept = module.collect_weather(["-6.2,106.8"], "history", "daily", ["temperature_2m_max"], "2026-11-01",
                                  "2026-11-03", drop_empty=False)
    assert len(kept["rows"]) == 3
    bad = module.collect_weather(["99,106"], "history", "daily", ["temperature_2m_max"], "2026-11-01", "2026-11-03")
    assert bad["rows"] == [] and "Koordinat di luar jangkauan" in bad["error"]


def test_nature_form_validation_and_rerun_seed(tmp_path):
    from datetime import date, timedelta
    from workspace_app.nature_view import check
    from workspace_app.views import validate
    today = date.today()
    base = {"locations": ["Bandung"], "mode": "history", "interval": "daily", "variables": ["precipitation_sum"],
            "start_date": today - timedelta(days=10), "end_date": today, "forecast_days": 7, "drop_empty": True}
    validate("openmeteo", base)
    validate("usgs", {"region": "indonesia", "min_magnitude": 4.5, "start_date": today - timedelta(days=10),
                      "end_date": today, "count": 0})
    for change, text in ((dict(locations=[]), "lokasi"), (dict(variables=[]), "variabel"),
                         (dict(end_date=today + timedelta(days=1)), "Prakiraan"),
                         (dict(start_date=today + timedelta(days=1), end_date=today), "Tanggal mulai"), (dict(start_date=date(1900, 1, 1)), "1940"),
                         (dict(interval="hourly", start_date=today - timedelta(days=500)), "1 tahun"),
                         (dict(locations=[str(n) for n in range(25)]), "Maksimal")):
        with __import__("pytest").raises(ValueError, match=text):
            check({**base, **change}, "openmeteo")
    quakes = {"region": "indonesia", "min_magnitude": 4.5, "start_date": today - timedelta(days=10),
              "end_date": today, "count": 0}
    for change, text in ((dict(start_date=today + timedelta(days=1), end_date=today), "Tanggal mulai"),
                         (dict(end_date=today + timedelta(days=1)), "melewati hari ini"),
                         (dict(start_date=date(1800, 1, 1)), "1900"), (dict(min_magnitude=11), "Magnitudo"),
                         (dict(region="custom", south=5, north=-5, west=90, east=100), "Lintang selatan"),
                         (dict(region="custom", south=-5, north=5, west=100, east=90), "Bujur barat"),
                         (dict(region="custom", south=None, north=5, west=90, east=100), "Isi batas")):
        with __import__("pytest").raises(ValueError, match=text):
            check({**quakes, **change}, "usgs")

    script = _page_script(tmp_path, """
from workspace_app.views import scraper_form, seed_form
from workspace_app.jobs import JobManager
if "seeded" not in st.session_state:
    st.session_state["seeded"] = True
    seed_form("openmeteo", {"locations": ["Bandung", "-6.2,106.8"], "mode": "history", "interval": "hourly",
                            "variables": ["temperature_2m"], "start_date": "2026-01-01", "end_date": "2026-01-03",
                            "forecast_days": 7, "drop_empty": False}, "Uji")
scraper_form(JobManager(PATH), "openmeteo", None, {})
""".replace("PATH", repr(str(tmp_path / "data"))))
    at = AppTest.from_file(script, default_timeout=30).run()
    assert not at.exception
    assert at.text_area(key="f-openmeteo-places").value == "Bandung\n-6.2,106.8"
    assert at.radio(key="f-openmeteo-interval").value == "hourly"
    assert at.multiselect(key="f-openmeteo-vars-hourly").value == ["temperature_2m"]
    assert str(at.date_input(key="f-openmeteo-start").value) == "2026-01-01"
    assert not at.checkbox(key="f-openmeteo-drop_empty").value


def test_nature_result_charts_render(tmp_path):
    script = _page_script(tmp_path, """
import pandas as pd
from workspace_app.nature_view import chart
quakes = pd.DataFrame({"waktu": ["2026-11-20T20:09:01+07:00", "2026-11-19T08:00:00+07:00"], "magnitudo": [5.2, 3.1],
                       "kedalaman_km": [10.0, 6.0], "lintang": [-3.88, 5.38], "bujur": [122.51, 125.21]})
weather = pd.DataFrame({"lokasi": ["Bandung", "Bandung", "Surabaya", "Surabaya"], "lintang": [-6.9] * 4,
                        "bujur": [107.6] * 4, "tanggal": ["2026-11-01", "2026-11-02"] * 2,
                        "suhu_maks_c": [30.1, 31.0, 33.0, 34.2], "hujan_mm": [1.0, 0.0, 0.2, 0.0]})
chart(quakes, "usgs")
chart(weather, "openmeteo")
""")
    at = AppTest.from_file(script, default_timeout=30).run()
    assert not at.exception
    assert [metric.value for metric in at.metric] == ["2", "5,2", "10 km"]
    assert at.selectbox(key="nature-chart").options == ["suhu_maks_c", "hujan_mm"]


def test_every_catalogue_source_has_an_icon_chip():
    import base64
    import re
    from workspace_app import ui

    def svg_of(chip):
        return base64.b64decode(re.search(r"base64,([A-Za-z0-9+/=]+)", chip).group(1)).decode()

    missing = [source for source in sources.LABELS if not ui.source_chip(source)]
    assert missing == [], f"sumber tanpa ikon: {missing}"
    brand, symbol = ui.source_chip("instagram"), ui.source_chip("news")
    assert 'class="source-chip brand"' in brand and "<img" in brand and 'fill="#E4405F"' in svg_of(brand)
    assert 'class="source-chip symbol"' in symbol and "--glyph:url(data:image/svg+xml" in symbol
    assert "<title>" not in svg_of(brand)  # the logo's own title text is dropped
    assert ui.source_chip("tidak-ada") == ""


def test_source_catalogue_is_listed_alphabetically(tmp_path, monkeypatch):
    manager = _seeded_manager(tmp_path, monkeypatch)  # gives some sources more jobs than others
    at = AppTest.from_file(_page_script(
        tmp_path, "from workspace_app.studio import scraping_page\nscraping_page(st.session_state['manager'])\n"),
        default_timeout=30)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception
    shown = [button.key.removeprefix("open-") for button in at.button if (button.key or "").startswith("open-")]
    expected = sorted(sources.LABELS, key=lambda key: sources.LABELS[key].casefold())
    assert shown == expected
