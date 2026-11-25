from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

pytest.importorskip("statsforecast", reason="the time-series packages are optional")
pytest.importorskip("statsmodels")
pytest.importorskip("arch")
pytest.importorskip("holidays")

from workspace_app import families as fam
from workspace_app import sources
from workspace_app import timeseries as ts
from workspace_app.jobs import JobManager


def seasonal(n=300, seed=0):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01", periods=n, freq="D")
    values = 100 + 0.05 * np.arange(n) + 5 * np.sin(2 * np.pi * np.arange(n) / 7) + rng.normal(0, 1, n)
    return pd.Series(values, index=index)


def random_walk(n=500, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=pd.date_range("2022-01-03", periods=n, freq="B"))


# --------------------------------------------------------------------------- #
# prepare
# --------------------------------------------------------------------------- #

def test_prepare_fills_small_gaps_and_counts_them():
    days = pd.date_range("2025-01-01", periods=40, freq="B")
    frame = pd.DataFrame({"tgl": days.strftime("%Y-%m-%d"), "close": np.arange(40.0)}).drop(index=[5, 6])
    prepared = ts.prepare(frame, "tgl", "close", freq="B")
    assert len(prepared.series) == 40 and prepared.filled == 2
    assert prepared.series.iloc[5] == pytest.approx(5.0)  # interpolated between 4 and 7


def test_prepare_counts_events_and_treats_quiet_periods_as_zero():
    frame = pd.DataFrame({"t": ["2025-01-01", "2025-01-01", "2025-01-04"]})
    prepared = ts.prepare(frame, "t", None, freq="D")
    assert prepared.series.tolist() == [2.0, 0.0, 0.0, 1.0] and prepared.filled == 0


def test_prepare_refuses_a_series_that_is_mostly_filled():
    frame = pd.DataFrame({"t": ["2025-01-01", "2025-06-01"], "v": [1, 2]})
    with pytest.raises(ts.NotEnough, match="tidak punya data"):
        ts.prepare(frame, "t", "v", freq="D")


def test_frequency_is_guessed_from_the_dates():
    assert ts.guess_frequency(pd.date_range("2025-01-01", periods=20, freq="D")) == "D"
    assert ts.guess_frequency(pd.date_range("2025-01-01", periods=20, freq="B")) == "B"
    assert ts.guess_frequency(pd.date_range("2025-01-01", periods=20, freq="W")) == "W"
    assert ts.guess_frequency(pd.date_range("2025-01-01", periods=20, freq="MS")) == "ME"


# --------------------------------------------------------------------------- #
# forecast
# --------------------------------------------------------------------------- #

def test_forecast_beats_the_naive_baseline_on_a_seasonal_series():
    result = ts.forecast(seasonal(), "D", 14)
    table = result["tabel"].set_index("kode")
    assert result["mengalahkan_naif"] and result["terbaik"] != ts.NAIVE
    assert table.loc[result["terbaik"], "MASE"] < table.loc[ts.NAIVE, "MASE"]
    assert table["MASE"].is_monotonic_increasing is False or result["tabel"]["MASE"].is_monotonic_increasing
    assert result["jendela"] == 3 and len(result["perkiraan"]) == 14
    future = result["perkiraan"]
    assert (future["Bawah 95%"] <= future["Bawah 80%"]).all() and (future["Atas 80%"] <= future["Atas 95%"]).all()
    assert future["Tanggal"].iloc[0] == pd.Timestamp("2024-01-01") + pd.Timedelta(days=300)
    text = ts.forecast_method_text(result, "uji", "D")
    assert "statsforecast" in text and "mengalahkan naif" in text and "rolling-origin" in text


def test_forecast_says_so_when_nothing_beats_a_random_walk():
    result = ts.forecast(random_walk(), "B", 20)
    assert not result["mengalahkan_naif"]
    assert "Tidak ada model yang mengalahkan baseline naif" in ts.forecast_method_text(result, "saham", "B")


def test_forecast_enforces_its_requirements():
    with pytest.raises(ts.NotEnough, match="minimal 30 titik"):
        ts.forecast(seasonal(20), "D", 3)
    with pytest.raises(ts.NotEnough, match="Horizon harus 1 sampai 60"):
        ts.forecast(seasonal(300), "D", 61)
    weekly = pd.Series(np.arange(60.0), index=pd.date_range("2024-01-07", periods=60, freq="W"))
    with pytest.raises(ts.NotEnough, match="siklus musiman"):  # 60 weeks is less than two years
        ts.forecast(weekly, "W", 5)
    assert ts.max_horizon(100) == 20


def test_backtest_windows_keep_two_cycles_of_training_data():
    assert ts.backtest_windows(300, 14, 7) == 3
    assert ts.backtest_windows(40, 10, 7) == 2  # (40 - 15) // 10
    with pytest.raises(ts.NotEnough):
        ts.backtest_windows(30, 20, 7)


# --------------------------------------------------------------------------- #
# decomposition, stationarity
# --------------------------------------------------------------------------- #

def test_decomposition_finds_trend_and_weekly_seasonality():
    frame, strengths = ts.decompose(seasonal(), "D")
    assert strengths["kekuatan_tren"] > 0.9 and strengths["kekuatan_musiman"] > 0.8 and strengths["periode"] == 7
    assert np.allclose(frame["Nilai"], frame["Tren"] + frame["Musiman"] + frame["Sisa"])
    noise = pd.Series(np.random.default_rng(2).normal(0, 1, 300), index=pd.date_range("2024-01-01", periods=300))
    assert ts.decompose(noise, "D")[1]["kekuatan_musiman"] < 0.3


def test_adf_and_kpss_agree_on_a_random_walk_and_on_noise():
    walk = ts.stationarity(random_walk(seed=2))  # a seed where both tests are decisive: at 5 % each test errs now and then
    assert walk["level"]["kesimpulan"] == "tidak stasioner" and walk["selisih"]["kesimpulan"] == "stasioner"
    noise = pd.Series(np.random.default_rng(3).normal(0, 1, 400))
    assert ts.stationarity(noise)["level"]["kesimpulan"] == "stasioner"
    text = ts.stationarity_text(walk)
    assert "ADF" in text and "KPSS" in text and "didiferensiasi" in text


# --------------------------------------------------------------------------- #
# Ramadan
# --------------------------------------------------------------------------- #

def food_prices(bump=0.10, seed=1):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-01", "2025-12-31", freq="D")
    log = np.log(12_000) + 0.0002 * np.arange(len(index)) + rng.normal(0, 0.01, len(index))
    for lebaran in ts.lebaran_dates(2022, 2025):
        day = pd.Timestamp(lebaran)
        log[(index >= day - pd.Timedelta(days=30)) & (index < day)] += bump
    return pd.Series(np.exp(log), index=index).asfreq("D")


def test_lebaran_dates_come_from_the_holidays_package():
    assert ts.lebaran_dates(2023, 2025) == [pd.Timestamp(d).date() for d in ("2023-04-22", "2024-04-10", "2025-03-31")]


def test_ramadan_effect_recovers_a_planted_price_rise_and_sees_none_in_flat_prices():
    table, test, _ = ts.ramadan_effect(food_prices())
    assert len(table) == 4 and table["Ramadan (%)"].between(7, 11).all()
    assert (table["Ramadan (%)"] > table["Sekitar Ramadan di luar itu (%)"] + 5).all()
    assert test["p"] < 0.001
    flat, flat_test, _ = ts.ramadan_effect(food_prices(bump=0.0))
    assert flat["Ramadan (%)"].abs().max() < 1.5
    text = ts.ramadan_method_text(table, test, "beras")
    assert "holidays" in text and "Mann–Whitney" in text and "bukan bukti" in text


def test_ramadan_needs_a_daily_series_that_covers_one_ramadan():
    with pytest.raises(ts.NotEnough, match="harian"):
        ts.ramadan_effect(seasonal(60))
    with pytest.raises(ts.NotEnough, match="Ramadan penuh"):
        ts.ramadan_effect(food_prices()["2022-06-01":"2022-12-31"])


# --------------------------------------------------------------------------- #
# stock
# --------------------------------------------------------------------------- #

def stock_prices(n=800, seed=1):
    rng = np.random.default_rng(seed)
    returns, vol = np.zeros(n), 0.01
    for i in range(n):
        vol = np.sqrt(0.000005 + 0.1 * (returns[i - 1] ** 2 if i else 0) + 0.85 * vol ** 2)
        returns[i] = vol * rng.standard_t(6) * 0.8
    return pd.Series(8000 * np.exp(np.cumsum(returns)), index=pd.date_range("2022-01-03", periods=n, freq="B"))


def test_return_summary_and_drawdown():
    prices = pd.Series([100.0, 110.0, 99.0, 120.0] * 10, index=pd.date_range("2025-01-01", periods=40, freq="B"))
    summary, returns, drawdown = ts.returns_summary(prices)
    assert summary["n"] == 39 and returns.iloc[0] == pytest.approx(np.log(1.1))
    assert summary["drawdown_maks"] == pytest.approx((99 / 120 - 1) * 100)  # from the 120 peak down to 99
    assert summary["return_total"] == pytest.approx(20.0)
    heavy, *_ = ts.returns_summary(stock_prices())
    assert heavy["kurtosis_lebih"] > 0.5 and heavy["jarque_bera_p"] < 0.05  # fat tails, not normal


def test_indicators_follow_their_definitions():
    prices = pd.Series(np.arange(1.0, 101.0), index=pd.date_range("2025-01-01", periods=100))
    frame = ts.indicators(prices)
    assert frame["MA20"].iloc[19] == pytest.approx(prices.iloc[:20].mean()) and np.isnan(frame["MA20"].iloc[18])
    assert frame["RSI14"].dropna().iloc[-1] == pytest.approx(100.0)  # only gains
    assert frame["MACD"].iloc[-1] > 0 and (frame["Histogram"] == frame["MACD"] - frame["Sinyal"]).all()
    mixed = ts.indicators(stock_prices())
    assert mixed["RSI14"].dropna().between(0, 100).all()


def test_garch_finds_persistent_volatility_and_needs_enough_returns():
    conditional, fit, projected = ts.garch(stock_prices())
    assert 0.5 < fit["persistensi"] < 1 and fit["nu"] > 2 and len(projected) == 10
    assert len(conditional) == 799 and (conditional > 0).all()
    with pytest.raises(ts.NotEnough, match="250"):
        ts.garch(stock_prices(100))
    assert "GARCH(1,1)" in ts.stock_method_text(ts.returns_summary(stock_prices())[0], fit, "BBCA")


# --------------------------------------------------------------------------- #
# weather
# --------------------------------------------------------------------------- #

def temperatures(seed=1):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2021-01-01", "2025-12-31", freq="D")
    values = 27 + 3 * np.sin(2 * np.pi * (index.dayofyear - 100) / 365) + rng.normal(0, 0.8, len(index))
    series = pd.Series(values, index=index)
    series["2024-07-01":"2024-07-10"] += 5
    return series


def test_anomalies_flag_a_planted_heat_wave_against_the_seasonal_mean():
    frame, label = ts.anomalies(temperatures(), "D")
    assert "hari dalam tahun" in label and abs(frame["Anomali"].mean()) < 0.05
    worst = frame["Anomali"].idxmax()
    assert pd.Timestamp("2024-07-01") <= worst <= pd.Timestamp("2024-07-10") and frame["Anomali"].max() > 4
    short, short_label = ts.anomalies(temperatures()["2024-01-01":"2025-06-30"], "D")
    assert "per bulan" in short_label  # fewer than three years: month means
    with pytest.raises(ts.NotEnough, match="satu tahun"):
        ts.anomalies(temperatures()["2024-01-01":"2024-04-01"], "D")
    assert "bukan normal iklim" in ts.anomaly_method_text(frame, label, "suhu_rata_c · Jakarta")


# --------------------------------------------------------------------------- #
# hub and pages
# --------------------------------------------------------------------------- #

def test_family_d_recommends_only_what_fits_the_columns():
    stock = pd.DataFrame({"date": ["2025-01-01"], "symbol": ["BBCA"], "close": [9000.0], "adj_close": [9000.0]})
    keys = {item.key: item for _, item in fam.recommendations(["D"], fam.profile(stock))}
    assert keys["saham"].ready and keys["forecast"].page == "deret-waktu" and "ramadan" not in keys
    assert "cuaca" not in keys
    food = pd.DataFrame({"tanggal": ["2025-01-01"], "komoditas": ["Beras"], "harga": [12000.0]})
    keys = {item.key for _, item in fam.recommendations(["D"], fam.profile(food))}
    assert "ramadan" in keys and "saham" not in keys
    weather = pd.DataFrame({"tanggal": ["2025-01-01"], "suhu_rata_c": [27.0], "hujan_mm": [3.0]})
    assert "cuaca" in {item.key for _, item in fam.recommendations(["D"], fam.profile(weather))}
    undated = pd.DataFrame({"close": [1.0]})
    items = {item.key: item for _, item in fam.recommendations(["D"], fam.profile(undated))}
    assert items["forecast"].blocked == "Tidak ada kolom tanggal." and not items["forecast"].ready


def page(tmp_path, name="series"):
    script = tmp_path / f"{name}.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                      "import streamlit as st\nfrom workspace_app.series_view import series_page\n"
                      "series_page(st.session_state['manager'])\n")
    return str(script)


def seeded(tmp_path, monkeypatch, source, rows, project="Uji"):
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / f"data-{source}")
    manager.start(source, {"keyword": "uji"}, project=project)
    manager._thread.join(60)
    return manager


def run_page(tmp_path, manager, name):
    at = AppTest.from_file(page(tmp_path, name), default_timeout=180)
    at.session_state["manager"] = manager
    return at.run()


def test_stock_page_opens_on_the_stock_view_with_garch_and_a_method_paragraph(tmp_path, monkeypatch):
    prices = stock_prices()
    rows = [{"date": day.strftime("%Y-%m-%d"), "symbol": "BBCA.JK", "close": float(value), "adj_close": float(value),
             "volume": 1000} for day, value in prices.items()]
    manager = seeded(tmp_path, monkeypatch, "saham", rows)
    at = run_page(tmp_path, manager, "stock")
    assert not at.exception, at.exception
    assert at.title[0].value == "Deret Waktu"
    assert any("GARCH(1,1)" in code.value for code in at.code)
    assert any(metric.label == "Volatilitas tahunan" for metric in at.metric)


def test_food_price_page_opens_on_ramadan_and_forecast_runs_on_demand(tmp_path, monkeypatch):
    prices = food_prices()
    rows = [{"tanggal": day.strftime("%Y-%m-%d"), "provinsi": "DKI Jakarta", "komoditas": "Beras",
             "harga": float(value)} for day, value in prices.items()]
    manager = seeded(tmp_path, monkeypatch, "pangan", rows)
    at = run_page(tmp_path, manager, "food")
    assert not at.exception, at.exception
    assert any("holidays" in code.value and "Mann–Whitney" in code.value for code in at.code)
    section = next(control for control in at.segmented_control if control.key.startswith("ts-section-"))
    section.set_value("Forecast").run()
    assert not at.exception, at.exception
    assert any("statsforecast" in code.value for code in at.code)
    assert any(metric.label == "Model terbaik" for metric in at.metric)


def test_weather_and_event_count_pages_render(tmp_path, monkeypatch):
    temps = temperatures()
    rows = [{"lokasi": "Jakarta", "tanggal": day.strftime("%Y-%m-%d"), "suhu_rata_c": float(value),
             "hujan_mm": 2.0} for day, value in temps.items()]
    manager = seeded(tmp_path, monkeypatch, "openmeteo", rows)
    at = run_page(tmp_path, manager, "weather")
    assert not at.exception, at.exception
    assert any("bukan normal iklim" in code.value for code in at.code)

    rng = np.random.default_rng(5)
    quakes = [{"waktu": (pd.Timestamp("2025-01-01") + pd.Timedelta(days=int(day))).strftime("%Y-%m-%d"),
               "magnitudo": float(rng.uniform(3, 6))} for day in rng.integers(0, 200, 400)]
    manager = seeded(tmp_path, monkeypatch, "usgs", quakes)
    at = run_page(tmp_path, manager, "quakes")
    assert not at.exception, at.exception
    section = next(control for control in at.segmented_control if control.key.startswith("ts-section-"))
    assert section.value == "Forecast"  # no source-specific view: the general one opens
    assert any(metric.label == "Model terbaik" for metric in at.metric) or at.info or at.warning
