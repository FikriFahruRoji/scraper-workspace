import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import families as fam
from workspace_app import geo
from workspace_app import sources
from workspace_app.jobs import JobManager

ROOT = Path(__file__).resolve().parents[1]


def blob(rng, centre, spread, count):
    return np.column_stack([rng.normal(centre[0], spread, count), rng.normal(centre[1], spread, count)])


def three_hotspots(seed=1):
    rng = np.random.default_rng(seed)
    return np.vstack([blob(rng, (-6.2, 106.8), 0.1, 400), blob(rng, (-7.8, 110.4), 0.15, 300),
                      blob(rng, (3.5, 98.7), 0.1, 200),
                      np.column_stack([rng.uniform(-10, 5, 300), rng.uniform(95, 141, 300)])])


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #

def test_coordinates_drop_what_cannot_be_a_place_and_say_how_many():
    frame = pd.DataFrame({"lintang": ["-6.2", "abc", None, 95, 10, 0, -7.5], "bujur": [106.8, 1, 2, 10, 200, 0, 110.4]})
    found, report = geo.coordinates(frame)
    assert found["_lat"].tolist() == [-6.2, -7.5] and found["_lon"].tolist() == [106.8, 110.4]
    assert report == {"baris": 7, "dipakai": 2, "kosong": 2, "di_luar_jangkauan": 2, "nol_nol": 1,
                      "kolom_lintang": "lintang", "kolom_bujur": "bujur"}
    with pytest.raises(geo.NotEnough, match="tidak punya kolom"):
        geo.coordinates(pd.DataFrame({"x": [1]}))
    with pytest.raises(geo.NotEnough, match="valid"):
        geo.coordinates(pd.DataFrame({"lat": [None], "lon": [None]}))
    assert "dibuang: 2 tanpa koordinat" in geo.density_method_text(report, "data USGS", 25)


# --------------------------------------------------------------------------- #
# Regions
# --------------------------------------------------------------------------- #

def scraper_locate():
    spec = importlib.util.spec_from_file_location("bencana_scraper", ROOT / "scrapers" / "bencana" / "scraper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_regions_put_known_places_in_their_kabupaten_and_far_sea_nowhere():
    got = geo.assign_regions([-6.2, -7.5, -9.5, 5.0], [106.8, 110.0, 108.0, 95.0])
    assert got.loc[0, "kabupaten_kota"] == "Kota Jakarta Barat" and got.loc[0, "provinsi"] == "DKI Jakarta"
    assert got.loc[1, "kabupaten_kota"] == "Kabupaten Wonosobo" and got.loc[1, "jarak_wilayah_km"] == 0
    assert got.loc[2, "kabupaten_kota"] == "" and np.isnan(got.loc[2, "jarak_wilayah_km"])  # open sea south of Java


def test_regions_agree_with_the_bencana_scrapers_own_lookup():
    scraper = scraper_locate()
    rng = np.random.default_rng(0)
    lat, lon = rng.uniform(-10.5, 5.5, 600), rng.uniform(95, 141, 600)
    got = geo.assign_regions(lat, lon)
    same, total, gaps = 0, 0, []
    for number, (a, b) in enumerate(zip(lat, lon)):
        name, province, distance = scraper.locate(a, b)
        mine = got.loc[number]
        if name or mine["kabupaten_kota"]:
            total += 1
            same += name == mine["kabupaten_kota"]
            if name == mine["kabupaten_kota"] and distance is not None:
                gaps.append(abs(distance - mine["jarak_wilayah_km"]))
    assert total > 100 and same / total >= 0.99  # a point on the line between two kabupaten may go either way
    assert max(gaps) <= 1.0  # the nearest-outline search is accurate to about a kilometre
    assert (got["jarak_wilayah_km"].dropna() >= 0).all()


def test_region_table_counts_weights_and_separates_inside_from_coast_and_far():
    got = geo.assign_regions([-6.2, -6.2001, -7.5, 0.0], [106.8, 106.8001, 110.0, 150.0])
    frame = pd.DataFrame({"mag": [5.0, 4.0, 3.0, 6.0]}).join(got)
    table, summary = geo.region_table(frame, "kabupaten_kota", "mag")
    assert table.iloc[0]["Kabupaten/kota"] == "Kota Jakarta Barat" and table.iloc[0]["Kejadian"] == 2
    assert table.iloc[0]["Jumlah mag"] == 9 and table.iloc[0]["Rata-rata mag"] == 4.5
    assert summary == {"total": 4, "di_wilayah": 3, "di_laut": 0, "luar": 1}
    by_province, _ = geo.region_table(frame, "provinsi")
    assert set(by_province["Provinsi"]) == {"DKI Jakarta", "Jawa Tengah"}
    text = geo.region_method_text(summary, "data USGS")
    assert "geoBoundaries" in text and "25,0%" in text
    assert geo.region_table(frame.assign(kabupaten_kota=""), "kabupaten_kota")[0].empty


# --------------------------------------------------------------------------- #
# Clusters
# --------------------------------------------------------------------------- #

def test_hdbscan_finds_the_planted_hotspots_and_calls_the_background_noise():
    points = three_hotspots()
    labels, table, summary = geo.cluster_points(points[:, 0], points[:, 1], 30)
    assert summary["klaster"] == 3 and summary["dikelompokkan"] == 1200 and not summary["diambil_sampel"]
    centres = {(round(row["Lintang pusat"], 0), round(row["Bujur pusat"], 0)) for _, row in table.iterrows()}
    assert centres == {(-6.0, 107.0), (-8.0, 110.0), (3.0, 99.0)}
    assert table["Titik"].sum() + summary["noise"] == 1200 and (labels >= -1).all()
    background = labels[900:]
    assert (background == -1).mean() > 0.7  # uniform scatter is mostly noise
    again = geo.cluster_points(points[:, 0], points[:, 1], 30)[0]
    assert (again == labels).all()  # deterministic


def test_clustering_samples_large_data_with_a_seed_and_checks_requirements():
    points = three_hotspots()
    labels, _, summary = geo.cluster_points(points[:, 0], points[:, 1], 30, limit=600)
    assert summary["diambil_sampel"] and summary["dikelompokkan"] == 600 and (labels == -2).sum() == 600
    assert "dipilih acak (seed 42)" in geo.cluster_method_text(summary, "data FIRMS")
    with pytest.raises(geo.NotEnough, match="minimal 30"):
        geo.cluster_points([0.0] * 10, [0.0] * 10)
    with pytest.raises(geo.NotEnough, match="2 atau lebih"):
        geo.cluster_points(points[:, 0], points[:, 1], 1)


# --------------------------------------------------------------------------- #
# Earthquakes
# --------------------------------------------------------------------------- #

def catalogue(b=1.0, mc=3.0, count=5000, seed=1, incomplete=150):
    rng = np.random.default_rng(seed)
    complete = np.round(mc + rng.exponential(1 / (b * math.log(10)), count), 1)
    small = np.round(rng.uniform(1.5, mc, incomplete), 1)
    return np.concatenate([complete, small])


def test_b_value_is_recovered_from_a_catalogue_with_known_b_and_an_incomplete_tail():
    result = geo.gutenberg_richter(catalogue())
    assert result["b"] == pytest.approx(1.0, abs=0.08) and result["sigma_b"] < 0.05
    assert 3.0 <= result["mc"] <= 3.5 and result["n_di_atas_mc"] < result["n"]
    flat = geo.gutenberg_richter(catalogue(b=0.6))
    assert flat["b"] == pytest.approx(0.6, abs=0.08) and flat["b"] < result["b"]
    curve = result["kurva"]
    assert curve["Kumulatif (≥ M)"].iloc[0] == result["n"] and curve["Kumulatif (≥ M)"].is_monotonic_decreasing
    text = geo.gutenberg_richter_text(result, "data USGS")
    assert "Aki (1965)" in text and "Shi & Bolt" in text and "Wiemer" in text and "kurvatur maksimum" in text


def test_b_value_follows_a_chosen_mc_and_is_withheld_with_too_few_quakes():
    mags = catalogue()
    higher = geo.gutenberg_richter(mags, mc=4.0)
    assert higher["mc"] == 4.0 and higher["n_di_atas_mc"] < geo.gutenberg_richter(mags)["n_di_atas_mc"]
    few = geo.gutenberg_richter(mags, mc=7.0)
    assert few["b"] is None and "tidak dihitung" in geo.gutenberg_richter_text(few, "data USGS")
    with pytest.raises(geo.NotEnough, match="minimal 50"):
        geo.gutenberg_richter(mags[:30])


def test_depth_classes_and_statistics():
    table, stats = geo.depth_summary([10, 20, 100, 400, "x"])
    assert table["Gempa"].tolist() == [2, 1, 1] and table["Persen"].tolist() == [50.0, 25.0, 25.0]
    assert stats["median"] == 60 and stats["maks"] == 400 and stats["n"] == 4
    with pytest.raises(geo.NotEnough):
        geo.depth_summary(["x"])


# --------------------------------------------------------------------------- #
# Hub and page
# --------------------------------------------------------------------------- #

def test_family_f_recommends_only_what_the_columns_allow():
    quakes = pd.DataFrame({"waktu": ["2025-01-01"] * 60, "magnitudo": [4.0] * 60, "lintang": [-6.2] * 60,
                           "bujur": [106.8] * 60})
    items = {item.key: item for _, item in fam.recommendations(["F"], fam.profile(quakes))}
    assert all(items[key].ready and items[key].page == "geospasial" for key in ("peta", "klaster", "wilayah", "gempa"))
    places = pd.DataFrame({"nama": ["Kopi Kita"] * 40, "lintang": [-6.2] * 40, "bujur": [106.8] * 40})
    keys = {item.key: item for _, item in fam.recommendations(["F"], fam.profile(places))}
    assert "gempa" not in keys and keys["klaster"].ready
    tiny = pd.DataFrame({"lintang": [-6.2] * 5, "bujur": [106.8] * 5, "magnitudo": [4.0] * 5})
    keys = {item.key: item for _, item in fam.recommendations(["F"], fam.profile(tiny))}
    assert keys["gempa"].blocked and "50" in keys["gempa"].blocked and keys["klaster"].blocked


def page(tmp_path, name="geo"):
    script = tmp_path / f"{name}.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(ROOT)!r})\n"
                      "import streamlit as st\nfrom workspace_app.geo_view import geo_page\n"
                      "geo_page(st.session_state['manager'])\n")
    return str(script)


def seeded(tmp_path, monkeypatch, source, rows):
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / f"data-{source}")
    manager.start(source, {"keyword": "uji"}, project="Uji")
    manager._thread.join(60)
    return manager


def quake_rows(seed=2):
    rng = np.random.default_rng(seed)
    points = np.vstack([blob(rng, (-7.8, 110.4), 0.3, 250), blob(rng, (-0.9, 100.4), 0.4, 200)])
    magnitudes = catalogue(count=450, incomplete=0, seed=seed)
    return [{"waktu": f"2025-01-{1 + n % 28:02d}T00:00:00+07:00", "magnitudo": float(magnitudes[n]),
             "kedalaman_km": float(abs(rng.normal(40, 30))), "lintang": float(points[n, 0]),
             "bujur": float(points[n, 1]), "tempat": "uji"} for n in range(450)]


def test_page_opens_on_earthquakes_and_every_section_renders(tmp_path, monkeypatch):
    manager = seeded(tmp_path, monkeypatch, "usgs", quake_rows())
    at = AppTest.from_file(page(tmp_path), default_timeout=120)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Peta Kejadian"
    section = next(control for control in at.segmented_control if control.key.startswith("geo-section-"))
    assert section.value == "Gempa"
    assert any("Gutenberg–Richter" in code.value and "Aki (1965)" in code.value for code in at.code)
    assert any(metric.label == "Nilai b" for metric in at.metric)
    for name, marker in (("Peta kepadatan", "HexagonLayer"), ("Klaster", "HDBSCAN"), ("Per wilayah", "geoBoundaries")):
        current = next(control for control in at.segmented_control if control.key.startswith("geo-section-"))
        current.set_value(name).run()  # fetched again each time: the widget of the previous run is stale
        assert not at.exception, (name, at.exception)
        assert any(marker in code.value for code in at.code), name
    assert at.get("deck_gl_json_chart") or at.code  # the map element is part of the cluster view too


def test_page_explains_a_dataset_without_coordinates(tmp_path, monkeypatch):
    manager = seeded(tmp_path, monkeypatch, "news", [{"title": "judul", "content": "isi artikel"}] * 3)
    at = AppTest.from_file(page(tmp_path, "nocoords"), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert any("tidak punya kolom lintang dan bujur" in info.value for info in at.info)
