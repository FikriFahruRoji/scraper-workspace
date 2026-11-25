import json
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from workspace_app import map_ui

ROOT = Path(__file__).resolve().parents[1]


def test_view_state_defaults_to_indonesia_when_majority_in_indonesia():
    idn = pd.DataFrame({"_lat": [-6.2, -7.8, 3.5, 40.7], "_lon": [106.8, 110.4, 98.7, -74.0]})
    assert map_ui.is_indonesia(idn["_lat"], idn["_lon"])
    view = map_ui.view_state(idn)
    assert view.latitude == -2.5
    assert view.longitude == 118.0
    assert view.zoom == 4.1

    abroad = pd.DataFrame({"_lat": [35.6, 37.7, 51.5], "_lon": [139.7, -122.4, -0.1]})
    assert not map_ui.is_indonesia(abroad["_lat"], abroad["_lon"])
    view_abroad = map_ui.view_state(abroad)
    assert view_abroad.latitude != -2.5


def test_point_map_uses_pixel_radius_and_dynamic_controls(tmp_path):
    script = tmp_path / "map_test.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import pandas as pd\n"
        "from workspace_app import map_ui\n"
        "df = pd.DataFrame({'lintang': [-6.2, -7.8], 'bujur': [106.8, 110.4], 'magnitudo': [4.2, 6.1], 'tempat': ['Jakarta', 'Yogyakarta']})\n"
        "map_ui.point_map(df, 'lintang', 'bujur', key_prefix='test', weight_col='magnitudo')\n"
    )
    at = AppTest.from_file(str(script), default_timeout=30).run()
    assert not at.exception, at.exception
    charts = at.get("deck_gl_json_chart")
    assert charts
    spec = json.loads(charts[0].json)
    assert spec["layers"][0]["radiusUnits"] == "pixels"
    assert spec["initialViewState"]["latitude"] == -2.5
    assert spec["initialViewState"]["longitude"] == 118.0

    at.selectbox(key="test-style").set_value("dark").run()
    assert not at.exception
    at.slider(key="test-px").set_value(9).run()
    assert not at.exception
