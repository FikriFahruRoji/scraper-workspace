from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import cross as cr
from workspace_app import sources
from workspace_app.jobs import JobManager

ROOT = Path(__file__).resolve().parents[1]


def _weather_and_food(n=60, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    rain = rng.gamma(2.0, 5.0, size=n)
    # Food price responds to rain with a 2-day lag + mild noise
    price = 12000.0 + np.roll(rain, 2) * 80.0 + rng.normal(0, 20.0, size=n)
    weather = pd.DataFrame({
        "tanggal": dates.strftime("%Y-%m-%d"),
        "provinsi": ["Jawa Barat"] * n,
        "hujan_mm": rain,
    })
    food = pd.DataFrame({
        "tanggal": dates.strftime("%Y-%m-%d"),
        "provinsi": ["Jawa Barat"] * n,
        "harga": price,
    })
    return weather, food


def test_extract_align_and_lag_correlation():
    weather, food = _weather_and_food()
    s_rain = cr.extract_series(weather, date_col="tanggal", value_col="hujan_mm",
                               region_col="provinsi", region_value="Jawa Barat", freq="D")
    s_price = cr.extract_series(food, date_col="tanggal", value_col="harga",
                                region_col="provinsi", region_value="Jawa Barat", freq="D")
    aligned = cr.align_series(s_rain, s_price, name_a="Hujan", name_b="Harga", freq="D")
    assert aligned["n_points"] == 60

    lag_res = cr.lag_correlation(aligned, max_lag=5, method="pearson")
    assert lag_res["best"] is not None
    assert lag_res["best"]["lag"] == 2
    assert lag_res["best"]["r"] > 0.7


def test_net_sentiment_and_count_series_extraction():
    dates = pd.date_range("2025-01-01", periods=30, freq="D")
    news = pd.DataFrame({
        "tanggal": dates.strftime("%Y-%m-%d"),
        "sentimen": ["positif" if i % 2 == 0 else "negatif" for i in range(30)],
    })
    s_vol = cr.extract_series(news, value_col=cr.COUNT_ROWS, freq="D")
    s_sent = cr.extract_series(news, value_col=cr.NET_SENTIMENT, freq="D")
    assert len(s_vol) == 30 and (s_vol == 1.0).all()
    assert set(s_sent.unique()) == {1.0, -1.0}


def test_granger_test_detects_predictive_direction_and_includes_caveat():
    weather, food = _weather_and_food(n=70)
    s_rain = cr.extract_series(weather, value_col="hujan_mm", freq="D")
    s_price = cr.extract_series(food, value_col="harga", freq="D")
    aligned = cr.align_series(s_rain, s_price, name_a="Hujan", name_b="Harga", freq="D")
    lag_res = cr.lag_correlation(aligned, max_lag=4)
    gr = cr.granger_test(aligned, max_lag=3, difference=True)
    rain_to_price = gr["results"][(gr["results"]["arah"] == "Hujan → Harga") & (gr["results"]["lag"] == 2)]
    assert not rain_to_price.empty and rain_to_price.iloc[0]["p_value"] < 0.01
    method = cr.cross_method_text(aligned, lag_res, gr)
    assert "bukan bukti hubungan sebab akibat" in method


def test_align_requires_minimum_overlap():
    s1 = pd.Series([1.0, 2.0], index=pd.date_range("2025-01-01", periods=2, freq="D"))
    s2 = pd.Series([3.0, 4.0], index=pd.date_range("2025-01-01", periods=2, freq="D"))
    with pytest.raises(cr.NotEnough, match="minimal 15"):
        cr.align_series(s1, s2, freq="D")


def _page(tmp_path, name="cross"):
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import streamlit as st\nfrom workspace_app.cross_view import cross_page\n"
        "cross_page(st.session_state['manager'])\n"
    )
    return str(script)


def test_cross_page_renders_with_two_datasets(tmp_path, monkeypatch):
    weather, food = _weather_and_food(n=45)
    manager = JobManager(tmp_path / "data-cross")
    for source, rows, proj in (("openmeteo", weather.to_dict("records"), "Cuaca Jabar"),
                               ("pangan", food.to_dict("records"), "Pangan Jabar")):
        monkeypatch.setattr(sources, "execute", lambda *a, _r=rows, **k: (_r, {}, None))
        manager.start(source, {"keyword": "uji"}, project=proj)
        manager._thread.join(60)

    at = AppTest.from_file(_page(tmp_path), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Lintas Dataset"
    assert any(m.label == "Titik beririsan" for m in at.metric)
