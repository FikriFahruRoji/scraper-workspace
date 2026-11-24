import json

import pytest

from workspace_app import sources
from workspace_app.oecd_view import check

STRUCTURE = {"data": {"dataStructures": [{"dataStructureComponents": {"dimensionList": {"dimensions": [
    {"id": "REF_AREA", "position": 0}, {"id": "FREQ", "position": 1}, {"id": "MEASURE", "position": 2}]}}}]}}
HEADER = ("STRUCTURE,STRUCTURE_ID,STRUCTURE_NAME,ACTION,REF_AREA,Reference area,FREQ,Frequency of observation,"
          "MEASURE,Measure,TIME_PERIOD,Time period,OBS_VALUE,Observation value,OBS_STATUS,Observation status,"
          "UNIT_MULT,Unit multiplier\n")


def line(area, name, freq, measure, label, period, value, status="A", mult=""):
    return (f"DATAFLOW,X,N,I,{area},{name},{freq},Annual,{measure},{label},{period},,{value},,{status},Normal,"
            f"{mult},\n")


CSV = (HEADER + line("IDN", "Indonesia", "A", "CPI", "Consumer price index", "2023", "3.67")
       + line("IDN", "Indonesia", "A", "GDP", "Gross domestic product", "2023", "4.9")
       + line("MYS", "Malaysia", "A", "CPI", "Consumer price index", "2024", "")
       + line("MYS", "Malaysia", "A", "CPI", "Consumer price index", "2024", "2.5"))


FLOW = "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,"


@pytest.fixture
def module():
    return sources.load("oecd")


def fake(monkeypatch, module, data=CSV, structure=STRUCTURE):
    urls = []

    def get(url, **_kw):
        urls.append(url)
        if "/dataflow/" in url:
            if isinstance(structure, Exception):
                raise structure
            return json.dumps(structure).encode()
        if isinstance(data, Exception):
            raise data
        return data.encode()

    monkeypatch.setattr(module, "get", get)
    return urls


def test_key_has_one_slot_per_dimension(module):
    dims = ["REF_AREA", "FREQ", "MEASURE"]
    assert module.build_key(dims, ["IDN", "MYS"], {"FREQ": ["A"]}) == "IDN+MYS.A."
    assert module.build_key(dims, [], {}) == ".."
    with pytest.raises(module.ApiError, match="NOPE"):
        module.build_key(dims, [], {"NOPE": ["X"]})


def test_rows_drop_constant_dimensions_and_skip_empty_values(monkeypatch, module):
    urls = fake(monkeypatch, module)
    result = module.collect_data("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,", ["IDN", "MYS"], 2020, 2025, {"FREQ": ["A"]})
    assert "/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,/IDN+MYS.A.?startPeriod=2020&endPeriod=2025" in urls[1]
    rows = result["rows"]
    assert len(rows) == 3 and result["error"] is None  # the empty observation is skipped
    first = rows[0]
    assert (first["negara_kode"], first["negara"], first["periode"], first["tahun"], first["nilai"]) == \
        ("IDN", "Indonesia", "2023", 2023, 3.67)
    assert first["measure"] == "Consumer price index" and first["measure_kode"] == "CPI"  # MEASURE varies
    assert "freq" not in first and result["tetap"] == {"FREQ": "Annual"}  # FREQ is constant: reported once
    assert "pengali" not in first  # no unit multiplier anywhere: column dropped
    assert result["dimensi"] == ["REF_AREA", "FREQ", "MEASURE"]


def test_count_limits_rows(monkeypatch, module):
    fake(monkeypatch, module)
    assert len(module.collect_data(FLOW, ["IDN"], 2020, 2025, {}, count=1)["rows"]) == 1


def test_errors_are_plain(monkeypatch, module):
    for text, expected in (("Server menjawab 404 (Not Found).", "tidak punya data"),
                           ("Server menjawab 429 (Too Many Requests).", "membatasi jumlah permintaan"),
                           ("Server menjawab 400 (Bad Request).", "menolak permintaan")):
        fake(monkeypatch, module, data=module.ApiError(text))
        result = module.collect_data(FLOW, ["IDN"], 2020, 2025, {})
        assert result["rows"] == [] and expected in result["error"]
    fake(monkeypatch, module, structure=module.ApiError("Server menjawab 404 (Not Found)."))
    assert "tidak ada" in module.collect_data(FLOW, ["IDN"], 2020, 2025, {})["error"]
    fake(monkeypatch, module, structure={"data": {}})
    assert "Struktur" in module.collect_data(FLOW, ["IDN"], 2020, 2025, {})["error"]


def test_flow_without_country_dimension_ignores_country_choice(monkeypatch, module):
    structure = {"data": {"dataStructures": [{"dataStructureComponents": {"dimensionList": {"dimensions": [
        {"id": "DONOR", "position": 0}, {"id": "RECIPIENT", "position": 1}]}}}]}}
    urls = fake(monkeypatch, module, data=HEADER, structure=structure)
    result = module.collect_data("OECD.DCD.FSD,DSD_DAC2@DF_DAC2A,1.7", ["IDN"], 2020, 2025, {"RECIPIENT": ["IDN"]})
    assert ",DSD_DAC2@DF_DAC2A,1.7/.IDN?" in urls[1] and "message" in result


def test_flow_parsing(module):
    assert module.normalize_flow(" OECD.SDD.TPS , DSD_PRICES@DF_PRICES_ALL ") == "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,"
    assert module.normalize_flow("A.B,C@D,1.0") == "A.B,C@D,1.0"
    for bad in ("", "OECD", "OECD.SDD,../x", "a b c"):
        with pytest.raises(ValueError):
            module.normalize_flow(bad)


CATALOGUE = {"data": {"dataflows": [
    {"id": "DF_B", "agencyID": "OECD.X", "version": "1.0", "name": " Beta data "},
    {"id": "DF_A", "agencyID": "OECD.Y", "version": "2.1", "name": "alpha data"}, {"id": "", "agencyID": "Z"}]}}


def test_catalogue_is_sorted_and_skips_broken_entries(monkeypatch, module):
    monkeypatch.setattr(module, "get", lambda url, **kw: json.dumps(CATALOGUE).encode())
    assert module.catalogue() == [{"flow": "OECD.Y,DF_A,2.1", "name": "alpha data"},
                                  {"flow": "OECD.X,DF_B,1.0", "name": "Beta data"}]


def _structure_payload():
    area = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=OECD:CL_AREA(1.0)"
    return {"data": {
        "dataStructures": [{"dataStructureComponents": {"dimensionList": {"dimensions": [
            {"id": "FREQ", "position": 1, "conceptIdentity": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=OECD.X:CS(1.0).FREQ"},
            {"id": "REF_AREA", "position": 0, "localRepresentation": {"enumeration": area},
             "conceptIdentity": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=OECD.X:CS(1.0).REF_AREA"}]}}}],
        "conceptSchemes": [{"agencyID": "OECD.X", "id": "CS", "version": "1.0", "concepts": [
            {"id": "REF_AREA", "name": "Reference area"}, {"id": "FREQ", "name": "Frequency"}]}],
        "codelists": [{"agencyID": "OECD", "id": "CL_AREA", "version": "1.0", "codes": [
            {"id": "IDN", "name": "Indonesia"}, {"id": "FRA", "name": "France"}, {"id": "ZZZ", "name": "Unused"}]}]}}


AVAILABLE = {"data": {"contentConstraints": [{"annotations": [{"id": "obs_count", "title": "1234"}],
    "cubeRegions": [{"keyValues": [{"id": "REF_AREA", "values": ["IDN", "FRA"]}, {"id": "FREQ", "values": ["A"]},
                                   {"id": "TIME_PERIOD", "timeRange": {"startPeriod": "2000"}}]}]}]}}


def test_structure_lists_only_codes_with_data(monkeypatch, module):
    def get(url, **_kw):
        return json.dumps(AVAILABLE if "availableconstraint" in url else _structure_payload()).encode()

    monkeypatch.setattr(module, "get", get)
    info = module.structure("OECD.X,DF,1.0")
    assert info["observations"] == 1234
    assert [dim["id"] for dim in info["dims"]] == ["REF_AREA", "FREQ"]  # ordered by position
    assert info["dims"][0]["name"] == "Reference area"
    assert info["dims"][0]["codes"] == [("IDN", "Indonesia"), ("FRA", "France")]  # ZZZ has no data
    assert info["dims"][1]["codes"] == [("A", "A")]  # no codelist: availability codes are used as they are


def test_structure_falls_back_to_the_full_codelist(monkeypatch, module):
    def get(url, **_kw):
        if "availableconstraint" in url:
            raise module.ApiError("Server menjawab 500 (Error).")
        return json.dumps(_structure_payload()).encode()

    monkeypatch.setattr(module, "get", get)
    info = module.structure("OECD.X,DF,1.0")
    assert info["observations"] is None and len(info["dims"][0]["codes"]) == 3


def test_check_validates_form(module):
    base = {"flow": "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "countries": ["IDN"], "start_year": 2015,
            "end_year": 2025, "filters": {"FREQ": ["A"]}}
    check(base)
    for change in ({"flow": ""}, {"start_year": 2026}, {"countries": ["ID N"]}, {"flow": "oops"},
                   {"filters": {"FREQ": []}}, {"filters": {"FREQ": ["A/B"]}}):
        with pytest.raises(ValueError):
            check({**base, **change})
    check({**base, "countries": []})  # empty means every area in the dataset


def test_form_offers_named_pickers_and_returns_clean_params(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from workspace_app import oecd_view
    monkeypatch.setattr(oecd_view, "_catalogue", lambda: [
        {"flow": "OECD.X,DF_B,1.0", "name": "Beta data"},
        {"flow": "OECD.ELS.HD,DSD_SHA@DF_SHA,1.1", "name": "Health expenditure and financing"}])
    monkeypatch.setattr(oecd_view, "_structure", lambda flow: {"observations": 5000, "dims": [
        {"id": "REF_AREA", "name": "Reference area", "codes": [("IDN", "Indonesia"), ("FRA", "France")]},
        {"id": "FREQ", "name": "Frequency", "codes": [("A", "Annual")]},
        {"id": "MEASURE", "name": "Measure", "codes": [("X", "Expenditure"), ("Y", "Revenue")]}]})
    script = f'''
import sys
sys.path.insert(0, "/Users/fikrifahruroji/Documents/Scraper")
import streamlit as st
from workspace_app.oecd_view import form
st.session_state["out"] = form("oecd")
'''
    app = AppTest.from_string(script, default_timeout=30).run()
    assert not app.exception
    dataset = app.selectbox(key="f-oecd-flow")
    assert dataset.options[0].startswith("★ Health expenditure") and len(dataset.options) == 2  # featured first
    assert app.multiselect(key="f-oecd-area-DSD_SHA-DF_SHA").value == ["IDN"]  # Indonesia preselected
    assert [m.label for m in app.multiselect if "Measure" in m.label]  # varying dimension is a picker
    assert not [m for m in app.multiselect if "Frequency" in m.label]  # a single-value dimension is not
    assert any("Frequency: Annual" in c.value for c in app.caption)  # ...but it is shown as fixed
    app.multiselect(key="f-oecd-dim-DSD_SHA-DF_SHA-MEASURE").set_value(["Y"]).run()
    out = app.session_state["out"]
    assert out["flow"] == "OECD.ELS.HD,DSD_SHA@DF_SHA,1.1" and out["countries"] == ["IDN"]
    assert out["filters"] == {"MEASURE": ["Y"]} and out["flow_name"] == "Health expenditure and financing"
