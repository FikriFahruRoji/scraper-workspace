from collections import Counter
from pathlib import Path
from string import Formatter

from streamlit.testing.v1 import AppTest

from workspace_app.i18n import _english


def test_translated_templates_keep_fields():
    def fields(template):
        return Counter(name for _, name, _, _ in Formatter().parse(template) if name is not None)

    for source, translated in _english().items():
        assert fields(source) == fields(translated), source


def test_language_switch_and_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_WORKSPACE_DATA", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "workspace_app.py"
    app = AppTest.from_file(str(script), default_timeout=20).run()
    assert app.selectbox(key="workspace-language").value == "id"
    app.selectbox(key="workspace-language").set_value("en").run()
    assert not app.exception
    assert app.query_params["lang"] == "en"
    assert app.title[0].value == "Collect Data"  # the start page, Lakukan Scraping
    reloaded = AppTest.from_file(str(script))
    reloaded.query_params["lang"] = "en"
    reloaded.run()
    assert reloaded.selectbox(key="workspace-language").value == "en"
    reloaded.selectbox(key="workspace-language").set_value("id").run()
    assert reloaded.title[0].value == "Lakukan Scraping"
