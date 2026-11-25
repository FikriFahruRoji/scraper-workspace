import math
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import families as fam
from workspace_app import optional
from workspace_app import panel as pn
from workspace_app import sources
from workspace_app.jobs import JobManager

ROOT = Path(__file__).resolve().parents[1]


def sample_panel_frame():
    rows = []
    countries = ["Indonesia", "Malaysia", "Thailand", "Vietnam", "Philippines", "Singapore"]
    for idx, country in enumerate(countries, start=1):
        for year_offset, year in enumerate([2020, 2021, 2022, 2023]):
            gdp = 100.0 * idx * (1.05 ** year_offset)
            infl = 2.0 + 0.3 * idx + 0.2 * year_offset
            unemp = 5.5 - 0.4 * idx + 0.1 * (year_offset % 2)
            rows.append({"negara": country, "tahun": year, "indikator": "PDB per kapita", "nilai": gdp})
            rows.append({"negara": country, "tahun": year, "indikator": "Inflasi (%)", "nilai": infl})
            rows.append({"negara": country, "tahun": year, "indikator": "Pengangguran (%)", "nilai": unemp})
    return pd.DataFrame(rows)


def test_prepare_cleans_invalid_rows_and_detects_oecd_columns():
    wb_bad = pd.DataFrame({
        "negara": ["Indonesia", "", None, "Malaysia"],
        "tahun": [2020, 2021, 2022, "abc"],
        "indikator": ["PDB", "PDB", "PDB", "PDB"],
        "nilai": [100.0, 200.0, 300.0, 400.0],
    })
    prepared = pn.prepare(wb_bad)
    assert prepared.report["baris"] == 4
    assert prepared.report["dipakai"] == 1
    assert prepared.report["dibuang"] == 3
    assert prepared.countries == ["Indonesia"]

    oecd = pd.DataFrame({
        "dataflow": ["QNA", "QNA"],
        "negara": ["Indonesia", "Japan"],
        "periode": ["2021-Q1", "2022-Q1"],
        "tahun": [2021, 2022],
        "nilai": [5.1, 2.3],
    })
    prep_oecd = pn.prepare(oecd)
    assert prep_oecd.indicators == ["QNA"]
    assert prep_oecd.countries == ["Indonesia", "Japan"]

    with pytest.raises(pn.NotEnough):
        pn.prepare(pd.DataFrame({"x": [1, 2]}))


def test_trend_cagr_computes_exact_growth_and_guards_single_year():
    prepared = pn.prepare(sample_panel_frame())
    res = pn.trend_cagr(prepared, "PDB per kapita", countries=["Indonesia", "Malaysia"])
    assert res["years"] == [2020, 2021, 2022, 2023]
    summary = res["summary"].set_index("negara")
    assert math.isclose(summary.loc["Indonesia", "cagr_persen"], 5.0, rel_tol=1e-4)
    assert "CAGR" in pn.trend_method_text("data World Bank", "PDB per kapita", res)

    one_year = pn.prepare(sample_panel_frame()[sample_panel_frame()["tahun"] == 2020])
    with pytest.raises(pn.NotEnough, match="minimal 2 tahun"):
        pn.trend_cagr(one_year, "PDB per kapita")


def test_indicator_correlation_pearson_and_spearman():
    prepared = pn.prepare(sample_panel_frame())
    res_p = pn.indicator_correlation(prepared, method="pearson")
    assert res_p["matrix"].shape == (3, 3)
    assert len(res_p["pairs"]) == 3
    assert "Pearson" in pn.correlation_method_text("data World Bank", res_p)

    res_s = pn.indicator_correlation(prepared, method="spearman", year=2022)
    assert res_s["method"] == "spearman"
    assert "tahun 2022" in pn.correlation_method_text("data World Bank", res_s)

    with pytest.raises(pn.NotEnough, match="minimal 2 indikator"):
        pn.indicator_correlation(prepared, indicators=["PDB per kapita"])


def test_cluster_countries_kmeans_and_minimum_countries():
    prepared = pn.prepare(sample_panel_frame())
    res = pn.cluster_countries(prepared, indicators=["PDB per kapita", "Inflasi (%)"], n_clusters=2, seed=42)
    assert res["n_countries"] == 6
    assert res["n_clusters"] == 2
    assert set(res["assignments"]["klaster"]) == {1, 2}
    assert "k-means" in pn.cluster_method_text("data World Bank", res)

    few = pn.prepare(sample_panel_frame()[sample_panel_frame()["negara"].isin(["Indonesia", "Malaysia", "Thailand"])])
    with pytest.raises(pn.NotEnough, match="minimal 5 negara"):
        pn.cluster_countries(few, n_clusters=2)


def test_panel_regression_guards_missing_package_and_minimums(monkeypatch):
    prepared = pn.prepare(sample_panel_frame())
    monkeypatch.setattr(optional, "available", lambda group: False)
    with pytest.raises(pn.NotEnough, match="linearmodels"):
        pn.panel_regression(prepared, "PDB per kapita", ["Inflasi (%)"])


def test_family_e_recommendations_ready():
    frame = sample_panel_frame()
    info = fam.profile(frame)
    assert info.has_panel is True
    found, how = fam.detect({"source": "worldbank", "params": {}}, frame, info)
    assert found == ["E"]
    recs = fam.recommendations(found, info)
    ready_pages = {item.page for _, item in recs if item.ready}
    assert "panel-negara" in ready_pages


def _page(tmp_path, name="panel"):
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import streamlit as st\nfrom workspace_app.panel_view import panel_page\n"
        "panel_page(st.session_state['manager'])\n"
    )
    return str(script)


def _seeded(tmp_path, monkeypatch, source, rows):
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / f"data-{source}")
    manager.start(source, {"indikator": "uji"}, project="Panel Uji")
    manager._thread.join(60)
    return manager


def test_panel_page_renders_every_section(tmp_path, monkeypatch):
    manager = _seeded(tmp_path, monkeypatch, "worldbank", sample_panel_frame().to_dict("records"))
    at = AppTest.from_file(_page(tmp_path), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Panel Negara"
    for name in ("Tren & CAGR", "Korelasi", "Klaster negara", "Regresi panel"):
        current = next(c for c in at.segmented_control if c.key.startswith("pn-section-"))
        current.set_value(name).run()
        assert not at.exception, (name, at.exception)


def test_panel_page_explains_non_panel_dataset(tmp_path, monkeypatch):
    manager = _seeded(tmp_path, monkeypatch, "news", [{"title": "judul", "content": "isi"}] * 3)
    at = AppTest.from_file(_page(tmp_path, "nopanel"), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert any("tidak punya kolom panel" in info.value for info in at.info)
