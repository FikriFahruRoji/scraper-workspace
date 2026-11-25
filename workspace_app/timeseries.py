"""Analyses for time series (family D in docs/ANALISIS.md): plain functions, no Streamlit.

- `prepare` turns rows into one regular series (filter, aggregate per period, fill small gaps);
- `forecast` runs statsforecast models with a rolling backtest against the naive random walk;
- `decompose` and `stationarity` use statsmodels (STL, ADF, KPSS);
- `ramadan_effect` compares food prices around Ramadan and Lebaran with the rest of the year;
- `returns_summary`, `indicators` and `garch` cover stock prices; `anomalies` covers weather.
The libraries are optional (`optional.GROUPS["timeseries"]`); this module imports them where they are used.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import math
import warnings

import numpy as np
import pandas as pd

from workspace_app import analysis as an

# Period → (label, seasonal length). Seasonal length is the cycle the models look for.
FREQS = {"D": ("Harian", 7), "B": ("Hari kerja", 5), "W": ("Mingguan", 52), "ME": ("Bulanan", 12),
         "QE": ("Triwulan", 4)}
AGGREGATIONS = {"mean": "Rata-rata", "sum": "Jumlah", "last": "Nilai terakhir"}
MIN_POINTS = 30
MIN_CYCLES = 2
MAX_HORIZON_SHARE = 0.2
MAX_FILLED = 0.3  # a series with more filled gaps than this is mostly invented
MAX_POINTS = 2_500  # the models see at most the latest points: older data hardly changes a forecast
BACKTEST_WINDOWS = 3
LEVELS = (80, 95)
MIN_GARCH = 250
MAX_SEASONAL_LENGTH = 24  # AutoETS and AutoARIMA with longer cycles are slow; MSTL handles those
RAMADAN_DAYS = 30
LEBARAN_BEFORE, LEBARAN_AFTER = 7, 7
TRADING_DAYS = 252
NAIVE = "Naive"
MODEL_LABELS = {"Naive": "Naif (random walk)", "SeasonalNaive": "Naif musiman", "AutoETS": "AutoETS",
                "AutoARIMA": "AutoARIMA", "MSTL": "MSTL + ETS"}


class NotEnough(ValueError):
    """The data does not meet a requirement of the analysis; the message says which."""


def seasonal_length(freq):
    return FREQS[freq][1]


def guess_frequency(dates):
    """The period that fits the dates best: D, B (no weekends), W or ME."""
    days = pd.Series(pd.to_datetime(dates).dropna().sort_values().unique())
    if len(days) < 3:
        return "D"
    median = days.diff().dropna().dt.days.median()
    if median >= 25:
        return "ME"
    if median >= 6:
        return "W"
    if (days.dt.dayofweek < 5).all():
        return "B"
    return "D"


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #

@dataclass
class Prepared:
    series: pd.Series
    freq: str
    rows: int  # rows that went into the series
    filled: int  # periods without data that were filled
    note: str = ""

    @property
    def filled_share(self):
        return self.filled / len(self.series) if len(self.series) else 0.0


def prepare(frame, date_column, value_column=None, *, freq="D", agg="mean"):
    """One regular series from rows.

    `value_column` None counts rows per period (agg is then "sum"; a period without rows is a real zero).
    Otherwise periods without rows are filled: zero for sums, linear interpolation for means and last values
    (leading and trailing gaps are cut). Raises NotEnough when too much of the series would be filled."""
    dates = an.parse_dates(frame[date_column])
    counting = value_column is None
    if counting:
        values, agg = pd.Series(1.0, index=frame.index), "sum"
    else:
        values = pd.to_numeric(frame[value_column], errors="coerce")
    data = pd.DataFrame({"d": dates, "v": values}).dropna(subset=["d"])
    if value_column is not None:
        data = data.dropna(subset=["v"])
    if data.empty:
        raise NotEnough("Tidak ada baris dengan tanggal dan nilai yang terbaca.")
    grouper = data.set_index("d")["v"].sort_index()
    if freq == "B":
        grouper = grouper[grouper.index.dayofweek < 5]
    resampled = grouper.resample(freq)
    series = getattr(resampled, agg)()
    if agg == "sum":
        series = series.where(resampled.count() > 0, 0.0)
        # A period without events is a real zero when counting events, but a gap when summing a measured value.
        gaps = (resampled.count() == 0) & (not counting)
    else:
        gaps = series.isna()
        series = series.interpolate(limit_area="inside")
        series = series.dropna()
    gaps = gaps.reindex(series.index, fill_value=False)
    series = series.astype(float)
    filled = int(gaps.sum())
    prepared = Prepared(series, freq, len(data), filled)
    if prepared.filled_share > MAX_FILLED:
        raise NotEnough(f"{filled} dari {len(series)} periode tidak punya data (> {MAX_FILLED:.0%}). Pilih periode "
                        f"yang lebih longgar atau saring data lebih sempit.")
    return prepared


def check_length(series, freq, *, cycles=MIN_CYCLES):
    """Raise NotEnough unless the series has MIN_POINTS points and `cycles` seasonal cycles."""
    m = seasonal_length(freq)
    if len(series) < MIN_POINTS:
        raise NotEnough(f"Butuh minimal {MIN_POINTS} titik, deret ini {len(series)}.")
    if len(series) < cycles * m:
        raise NotEnough(f"Butuh minimal {cycles} siklus musiman ({cycles * m} titik untuk periode "
                        f"{FREQS[freq][0].lower()}), deret ini {len(series)}. Coba periode yang lebih halus.")


def max_horizon(n):
    return max(1, int(n * MAX_HORIZON_SHARE))


# --------------------------------------------------------------------------- #
# Forecast with backtest
# --------------------------------------------------------------------------- #

def _models(m):
    from statsforecast.models import AutoARIMA, AutoETS, MSTL, Naive, SeasonalNaive

    short = m <= MAX_SEASONAL_LENGTH
    models = [Naive(alias="Naive")]
    if m > 1:
        models.append(SeasonalNaive(m, alias="SeasonalNaive"))
    models.append(AutoETS(season_length=m if short else 1, alias="AutoETS"))
    # approximation=True is what R's auto.arima does for long series; without it the stepwise search sometimes
    # wanders into very slow fits (a daily series of 1,400 points took 38 s instead of 1 s).
    models.append(AutoARIMA(season_length=m if short else 1, approximation=True, alias="AutoARIMA"))
    if m > 1:
        models.append(MSTL(season_length=[m], trend_forecaster=AutoETS(model="ZZN"), alias="MSTL"))
    return models


def _long(series):
    return pd.DataFrame({"unique_id": "seri", "ds": series.index, "y": series.values})


def _mase_scale(train, m):
    lag = m if m > 1 and len(train) > m else 1
    diffs = np.abs(train[lag:] - train[:-lag])
    return float(diffs.mean()) if len(diffs) else float("nan")


def backtest_windows(n, horizon, m):
    """How many rolling windows (at most BACKTEST_WINDOWS) leave each training set at least 2 cycles long."""
    smallest = max(MIN_CYCLES * m, 15)
    windows = min(BACKTEST_WINDOWS, (n - smallest) // horizon)
    if windows < 1:
        raise NotEnough("Data terlalu pendek untuk uji mundur dengan horizon ini. Kecilkan horizon.")
    return windows


def forecast(series, freq, horizon):
    """Models compared on a rolling backtest of the latest data, then the best one refitted on everything.

    Backtest: `n_windows` windows of `horizon` steps at the end of the series (training sets grow, never see
    the future); metrics are MASE (scaled by the in-sample seasonal naive error), MAPE (only when no actual
    value is zero), RMSE, and the coverage of the 95 % interval. Returns a dict with the metric table, the
    forecast of the best model (with 80 and 95 % intervals) and the settings."""
    from statsforecast import StatsForecast
    import statsforecast

    check_length(series, freq)
    if not 1 <= horizon <= max_horizon(len(series)):
        raise NotEnough(f"Horizon harus 1 sampai {max_horizon(len(series))} "
                        f"({MAX_HORIZON_SHARE:.0%} dari panjang data).")
    series = series.iloc[-MAX_POINTS:]
    m = seasonal_length(freq)
    windows = backtest_windows(len(series), horizon, m)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(models=_models(m), freq=freq, n_jobs=1)
        cv = sf.cross_validation(df=_long(series), h=horizon, n_windows=windows, step_size=horizon, level=[95])
    names = [model for model in MODEL_LABELS if model in cv.columns]
    rows = []
    for name in names:
        mase, ape, squared, covered, count = [], [], [], 0, 0
        for cutoff, part in cv.groupby("cutoff"):
            train = series[series.index <= cutoff].to_numpy()
            scale = _mase_scale(train, m)
            error = part["y"].to_numpy() - part[name].to_numpy()
            mase.append(float(np.abs(error).mean() / scale) if scale and not math.isnan(scale) else float("nan"))
            squared.extend(error ** 2)
            if (part["y"] != 0).all():
                ape.extend(np.abs(error / part["y"].to_numpy()))
            covered += int(((part["y"] >= part[f"{name}-lo-95"]) & (part["y"] <= part[f"{name}-hi-95"])).sum())
            count += len(part)
        rows.append({"Model": MODEL_LABELS[name], "kode": name, "MASE": float(np.nanmean(mase)),
                     "MAPE (%)": float(np.mean(ape) * 100) if ape and len(ape) == count else None,
                     "RMSE": float(np.sqrt(np.mean(squared))), "Cakupan 95% (%)": covered / count * 100})
    table = pd.DataFrame(rows).sort_values("MASE", ignore_index=True)
    naive_mase = float(table.loc[table["kode"] == NAIVE, "MASE"].iloc[0])
    table["Selisih MASE vs naif (%)"] = (table["MASE"] / naive_mase - 1) * 100
    best = table.iloc[0]["kode"]
    beats = best != NAIVE and table.iloc[0]["MASE"] < naive_mase
    chosen = [model for model in _models(m) if model.alias == best]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = StatsForecast(models=chosen, freq=freq, n_jobs=1).forecast(
            df=_long(series), h=horizon, level=list(LEVELS))
    future = pd.DataFrame({"Tanggal": fitted["ds"], "Perkiraan": fitted[best]})
    for level in LEVELS:
        future[f"Bawah {level}%"] = fitted[f"{best}-lo-{level}"]
        future[f"Atas {level}%"] = fitted[f"{best}-hi-{level}"]
    return {"tabel": table, "terbaik": best, "mengalahkan_naif": bool(beats), "perkiraan": future,
            "horizon": horizon, "jendela": windows, "musiman": m, "titik": len(series),
            "versi": statsforecast.__version__, "nama_terbaik": MODEL_LABELS[best]}


def forecast_method_text(result, series_name, freq):
    from workspace_app.social import decimal

    table = result["tabel"]
    top = table.iloc[0]
    naive = table[table["kode"] == NAIVE].iloc[0]
    verdict = (f"Model terbaik, {result['nama_terbaik']}, mengalahkan naif dengan MASE {decimal(top['MASE'], 3)} "
               f"melawan {decimal(naive['MASE'], 3)}." if result["mengalahkan_naif"] else
               f"Tidak ada model yang mengalahkan baseline naif (MASE naif {decimal(naive['MASE'], 3)}), sehingga "
               f"perkiraannya tidak lebih baik daripada menganggap nilai berikutnya sama dengan terakhir; "
               f"perkiraan yang ditampilkan sebaiknya tidak dijadikan dasar keputusan.")
    seasonal = result["musiman"] > MAX_SEASONAL_LENGTH
    return (
        f"Deret {series_name} ({result['titik']} titik, periode {FREQS[freq][0].lower()}) diperkirakan "
        f"{result['horizon']} periode ke depan dengan pustaka statsforecast {result['versi']} (Garza dkk., 2022). "
        f"Lima model dibandingkan: naif (random walk), naif musiman (siklus {result['musiman']}), AutoETS, "
        f"AutoARIMA (pencarian bertahap dengan aproksimasi jumlah kuadrat bersyarat), dan MSTL dengan tren ETS" + (
            f"; untuk siklus panjang AutoETS dan AutoARIMA dijalankan tanpa komponen musiman" if seasonal else "")
        + f". Model dievaluasi "
        f"dengan uji mundur bergulir (rolling-origin) pada {result['jendela']} jendela sepanjang horizon di "
        f"ujung data, tiap jendela dilatih hanya dari data sebelumnya. Metriknya MASE (galat absolut dibagi "
        f"galat naif musiman di data latih; < 1 berarti lebih baik daripada naif), MAPE bila tidak ada nilai nol, "
        f"RMSE, dan cakupan selang 95%. {verdict} Perkiraan akhir memakai model dengan MASE terkecil yang "
        f"dilatih ulang pada seluruh data, dengan selang prediksi 80% dan 95% (Hyndman & Athanasopoulos, 2021)."
    )


# --------------------------------------------------------------------------- #
# Decomposition and stationarity
# --------------------------------------------------------------------------- #

def decompose(series, freq):
    """STL (robust) decomposition. Returns (frame with Nilai, Tren, Musiman, Sisa; strengths dict).

    Strength of trend and seasonality follow Wang, Smith & Hyndman (2006): 1 - Var(remainder) /
    Var(component + remainder), floored at 0."""
    from statsmodels.tsa.seasonal import STL

    check_length(series, freq)
    m = seasonal_length(freq)
    result = STL(series.iloc[-MAX_POINTS:], period=max(m, 2), robust=True).fit()
    frame = pd.DataFrame({"Nilai": result.observed, "Tren": result.trend, "Musiman": result.seasonal,
                          "Sisa": result.resid})

    def strength(component):
        denominator = np.var(component + frame["Sisa"])
        return float(max(0.0, 1 - np.var(frame["Sisa"]) / denominator)) if denominator else 0.0

    return frame, {"kekuatan_tren": strength(frame["Tren"]), "kekuatan_musiman": strength(frame["Musiman"]),
                   "periode": max(m, 2), "titik": len(frame)}


def stationarity(series):
    """ADF (H0: unit root) and KPSS (H0: stationary) on the series and on its first difference."""
    from statsmodels.tsa.stattools import adfuller, kpss

    if len(series) < MIN_POINTS:
        raise NotEnough(f"Butuh minimal {MIN_POINTS} titik, deret ini {len(series)}.")

    def run(values):
        values = pd.Series(values).dropna()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            adf_stat, adf_p, lags, *_ = adfuller(values, autolag="AIC")
            kpss_stat, kpss_p, *_ = kpss(values, regression="c", nlags="auto")
        adf_reject, kpss_reject = adf_p < 0.05, kpss_p < 0.05
        if adf_reject and not kpss_reject:
            verdict = "stasioner"
        elif not adf_reject and kpss_reject:
            verdict = "tidak stasioner"
        else:
            verdict = "belum tegas"
        return {"adf": float(adf_stat), "adf_p": float(adf_p), "lag": int(lags), "kpss": float(kpss_stat),
                "kpss_p": float(kpss_p), "kesimpulan": verdict, "n": len(values)}

    return {"level": run(series), "selisih": run(series.diff())}


def stationarity_text(result):
    from workspace_app.social import decimal, p_text

    level, diff = result["level"], result["selisih"]
    advice = {"stasioner": "Deret dapat dimodelkan langsung.",
              "tidak stasioner": "Deret perlu didiferensiasi (atau ditransformasi) sebelum dimodelkan dengan "
                                 "ARIMA; selisih pertamanya " + ("stasioner." if diff["kesimpulan"] == "stasioner"
                                                                else "belum tegas stasioner."),
              "belum tegas": "Kedua uji tidak sepakat: kemungkinan tren deterministik atau data terlalu pendek."}
    return (f"Uji ADF (autolag AIC): statistik {decimal(level['adf'], 2)}, p {p_text(level['adf_p'])}. Uji KPSS "
            f"(level, bandwidth otomatis): statistik {decimal(level['kpss'], 3)}, p {p_text(level['kpss_p'])}. "
            f"Kesimpulan: {level['kesimpulan']}. {advice[level['kesimpulan']]}")


def decompose_method_text(strengths, stat, series_name, freq):
    from workspace_app.social import decimal

    return (
        f"Deret {series_name} ({strengths['titik']} titik, periode {FREQS[freq][0].lower()}) diuraikan menjadi tren, "
        f"musiman (siklus {strengths['periode']}), dan sisa dengan STL yang robust terhadap pencilan (Cleveland "
        f"dkk., 1990; statsmodels). Kekuatan tren {decimal(strengths['kekuatan_tren'], 2)} dan kekuatan musiman "
        f"{decimal(strengths['kekuatan_musiman'], 2)} dihitung sebagai 1 dikurangi rasio varians sisa terhadap "
        f"varians komponen ditambah sisa (Wang, Smith, & Hyndman, 2006); nilainya 0 sampai 1. Kestasioneran diuji "
        f"dengan uji Augmented Dickey–Fuller (H0: ada akar unit; Dickey & Fuller, 1979) dan KPSS (H0: stasioner; "
        f"Kwiatkowski dkk., 1992) pada taraf 5%; kedua uji dipakai bersama karena kesimpulannya saling melengkapi. "
        f"{stationarity_text(stat)}")


# --------------------------------------------------------------------------- #
# Ramadan and Lebaran
# --------------------------------------------------------------------------- #

def lebaran_dates(first_year, last_year):
    """Date of Idul Fitri (1 Syawal) per year, from the `holidays` package."""
    import holidays

    found = {}
    for year in range(first_year, last_year + 1):
        for day, name in holidays.Indonesia(years=year, language="id").items():
            if name == "Hari Raya Idul Fitri":
                found[day] = year
    return sorted(found)


def ramadan_effect(series):
    """Price deviation in Ramadan and around Lebaran, as percent above or below the price expected without it.

    Expected price = centred rolling median of 91 days. Per year with full data around that Lebaran: the
    mean deviation in the 30 days of Ramadan before Idul Fitri, in the week before and after it, and outside
    both. Also a Mann–Whitney test of Ramadan days against all other days (all years together). Requires a
    daily series."""
    from scipy.stats import mannwhitneyu

    if len(series) < 120 or series.index.freqstr not in ("D", "B"):
        raise NotEnough("Efek Ramadan butuh deret harian dengan minimal 120 titik.")
    positive = series[series > 0]
    log = np.log(positive)
    window = 91 if series.index.freqstr == "D" else 65
    expected = log.rolling(window, center=True, min_periods=window // 2).median()
    deviation = ((log - expected) * 100).dropna()
    first, last = series.index.min().year, series.index.max().year
    rows, ramadan_all, other_all = [], [], []
    for day in lebaran_dates(first - 1, last + 1):
        lebaran = pd.Timestamp(day)
        start = lebaran - timedelta(days=RAMADAN_DAYS)
        before = lebaran - timedelta(days=LEBARAN_BEFORE)
        after = lebaran + timedelta(days=LEBARAN_AFTER)
        edge = timedelta(days=45)
        if start - edge < series.index.min() or after + edge > series.index.max():
            continue  # the data do not cover this Ramadan with enough context
        ramadan = deviation[(deviation.index >= start) & (deviation.index < before)]
        week = deviation[(deviation.index >= before) & (deviation.index <= after)]
        outside = deviation[(deviation.index < start) | (deviation.index > after)]
        outside = outside[(outside.index >= start - edge) & (outside.index <= after + edge)]
        if ramadan.empty or week.empty or outside.empty:
            continue
        ramadan_all.extend(ramadan.values)
        other_all.extend(outside.values)
        rows.append({"Tahun": lebaran.year, "Idul Fitri": lebaran.date(), "Ramadan (%)": float(ramadan.mean()),
                     "Pekan Lebaran (%)": float(week.mean()), "Sekitar Ramadan di luar itu (%)": float(outside.mean())})
    if not rows:
        raise NotEnough("Data tidak mencakup satu Ramadan penuh dengan 45 hari sebelum dan sesudahnya.")
    test = None
    if len(ramadan_all) >= 10 and len(other_all) >= 10:
        statistic, p_value = mannwhitneyu(ramadan_all, other_all, alternative="two-sided")
        test = {"u": float(statistic), "p": float(p_value), "n_ramadan": len(ramadan_all),
                "n_lain": len(other_all)}
    table = pd.DataFrame(rows)
    return table, test, deviation


def ramadan_method_text(table, test, series_name):
    from workspace_app.social import decimal, p_text

    years = ", ".join(str(year) for year in table["Tahun"])
    text = (
        f"Efek Ramadan dan Lebaran pada {series_name} diukur dari simpangan harga (dalam persen log) terhadap "
        f"median bergulir terpusat 91 hari, yang dipakai sebagai harga yang diharapkan tanpa efek musiman pendek. "
        f"Tanggal Idul Fitri diambil dari pustaka holidays (Indonesia); Ramadan didekati dengan {RAMADAN_DAYS} hari "
        f"sebelum Idul Fitri, dan pekan Lebaran dengan {LEBARAN_BEFORE} hari sebelum sampai {LEBARAN_AFTER} hari "
        f"sesudahnya. Hanya tahun dengan data minimal 45 hari sebelum dan sesudahnya yang dihitung ({years}). "
        f"Rata-rata simpangan di Ramadan {decimal(table['Ramadan (%)'].mean(), 1)}%, di pekan Lebaran "
        f"{decimal(table['Pekan Lebaran (%)'].mean(), 1)}%.")
    if test:
        text += (f" Simpangan harian di Ramadan dibandingkan dengan hari lain di sekitarnya dengan uji Mann–Whitney "
                 f"U: U = {decimal(test['u'], 0)}, p {p_text(test['p'])} (n = {test['n_ramadan']} dan "
                 f"{test['n_lain']}). Hari yang berdekatan saling berkorelasi, sehingga p cenderung terlalu kecil; "
                 f"baca tabel per tahun, bukan hanya uji ini.")
    return text + " Hasil ini korelasi waktu, bukan bukti bahwa Ramadan menyebabkan harga naik."


# --------------------------------------------------------------------------- #
# Stock prices
# --------------------------------------------------------------------------- #

def log_returns(prices):
    prices = prices[prices > 0]
    return np.log(prices).diff().dropna()


def returns_summary(prices):
    """Distribution of daily log returns, with annualised volatility and maximum drawdown."""
    from scipy.stats import jarque_bera

    returns = log_returns(prices)
    if len(returns) < MIN_POINTS:
        raise NotEnough(f"Butuh minimal {MIN_POINTS} return, deret ini {len(returns)}.")
    statistic, p_value = jarque_bera(returns)
    running_max = prices.cummax()
    drawdown = (prices / running_max - 1) * 100
    return {
        "n": len(returns), "rata_rata_harian": float(returns.mean() * 100), "simpangan_baku_harian": float(returns.std() * 100),
        "volatilitas_tahunan": float(returns.std() * math.sqrt(TRADING_DAYS) * 100),
        "skewness": float(returns.skew()), "kurtosis_lebih": float(returns.kurt()),
        "jarque_bera": float(statistic), "jarque_bera_p": float(p_value),
        "return_total": float((prices.iloc[-1] / prices.iloc[0] - 1) * 100),
        "drawdown_maks": float(drawdown.min()), "tanggal_drawdown": drawdown.idxmin(),
    }, returns, drawdown


def indicators(prices):
    """Moving averages (20, 50), RSI(14, Wilder), and MACD(12, 26, 9) written with pandas."""
    frame = pd.DataFrame({"Harga": prices})
    frame["MA20"] = prices.rolling(20).mean()
    frame["MA50"] = prices.rolling(50).mean()
    change = prices.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    relative = gain / loss.replace(0, np.nan)
    frame["RSI14"] = (100 - 100 / (1 + relative)).where(loss != 0, 100.0)
    fast, slow = prices.ewm(span=12, adjust=False).mean(), prices.ewm(span=26, adjust=False).mean()
    frame["MACD"] = fast - slow
    frame["Sinyal"] = frame["MACD"].ewm(span=9, adjust=False).mean()
    frame["Histogram"] = frame["MACD"] - frame["Sinyal"]
    return frame


def garch(prices, horizon=10):
    """GARCH(1,1) with Student-t errors on daily log returns (in percent) with the `arch` package.

    Returns (conditional volatility Series in percent per day, summary dict, volatility forecast list)."""
    from arch import arch_model
    import arch

    returns = log_returns(prices) * 100
    if len(returns) < MIN_GARCH:
        raise NotEnough(f"GARCH butuh minimal {MIN_GARCH} return harian, data ini {len(returns)}.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = arch_model(returns, mean="Constant", vol="GARCH", p=1, q=1, dist="t").fit(disp="off")
        projected = fit.forecast(horizon=horizon).variance.iloc[-1].to_numpy() ** 0.5
    params = fit.params
    alpha, beta = float(params["alpha[1]"]), float(params["beta[1]"])
    persistence = alpha + beta
    long_run = float(params["omega"] / (1 - persistence)) ** 0.5 if persistence < 1 else float("nan")
    return fit.conditional_volatility, {
        "omega": float(params["omega"]), "alpha": alpha, "beta": beta, "nu": float(params["nu"]),
        "persistensi": persistence, "volatilitas_jangka_panjang": long_run, "n": len(returns),
        "loglik": float(fit.loglikelihood), "aic": float(fit.aic), "bic": float(fit.bic),
        "versi": arch.__version__, "p_alpha": float(fit.pvalues["alpha[1]"]), "p_beta": float(fit.pvalues["beta[1]"]),
    }, list(map(float, projected))


def stock_method_text(summary, fit, series_name):
    from workspace_app.social import decimal, p_text

    text = (
        f"Return harian {series_name} dihitung sebagai selisih logaritma harga penutupan ({summary['n']} return). "
        f"Volatilitas tahunan = simpangan baku return harian × √{TRADING_DAYS} ({decimal(summary['volatilitas_tahunan'], 1)}%); "
        f"kenormalan diuji dengan Jarque–Bera (statistik {decimal(summary['jarque_bera'], 1)}, p "
        f"{p_text(summary['jarque_bera_p'])}; kurtosis berlebih {decimal(summary['kurtosis_lebih'], 2)}). "
        f"Drawdown maksimum {decimal(summary['drawdown_maks'], 1)}%. Rata-rata bergerak 20 dan 50 hari, RSI 14 hari "
        f"(smoothing Wilder), dan MACD (12, 26, 9) dihitung dengan pandas; indikator teknikal hanya merangkum "
        f"harga lampau dan bukan sinyal beli atau jual.")
    if fit:
        text += (
            f" Volatilitas bersyarat dimodelkan dengan GARCH(1,1) (Bollerslev, 1986) berdistribusi galat Student-t "
            f"pada return dalam persen dengan pustaka arch {fit['versi']} (Sheppard, 2023): α = {decimal(fit['alpha'], 3)}, "
            f"β = {decimal(fit['beta'], 3)}, persistensi α + β = {decimal(fit['persistensi'], 3)}, derajat bebas "
            f"{decimal(fit['nu'], 1)}.")
    return text


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #

def anomalies(series, freq="D"):
    """Deviation from the average of the same time of year in this data.

    Daily data: the climatology is the mean per day of year smoothed over ±7 days when the data cover at
    least 3 years, else per month. Returns (frame with Nilai, Rata-rata periode, Anomali, Skor z),
    climatology label. The climatology is the average of the data at hand, not a 30-year normal."""
    if len(series) < MIN_POINTS:
        raise NotEnough(f"Butuh minimal {MIN_POINTS} titik, deret ini {len(series)}.")
    years = (series.index.max() - series.index.min()).days / 365.25
    if years < 1:
        raise NotEnough("Anomali butuh data minimal satu tahun penuh agar tiap musim terwakili.")
    if freq in ("D", "B") and years >= 3:
        key = np.minimum(series.index.dayofyear.to_numpy(), 365)  # 29 Feb counts with 31 Dec
        raw = series.groupby(key).agg(["mean", "std"]).reindex(range(1, 366))
        wrapped = pd.concat([raw.iloc[-7:], raw, raw.iloc[:7]])
        smooth = wrapped.rolling(15, center=True, min_periods=1).mean().iloc[7:-7]
        mean, std = smooth["mean"].reindex(key).to_numpy(), smooth["std"].reindex(key).to_numpy()
        label = "rata-rata per hari dalam tahun (dihaluskan ±7 hari)"
    else:
        key = series.index.month
        raw = series.groupby(key).agg(["mean", "std"])
        mean, std = raw["mean"].reindex(key).to_numpy(), raw["std"].reindex(key).to_numpy()
        label = "rata-rata per bulan"
    frame = pd.DataFrame({"Nilai": series, "Rata-rata periode": mean}, index=series.index)
    frame["Anomali"] = frame["Nilai"] - frame["Rata-rata periode"]
    frame["Skor z"] = frame["Anomali"] / np.where(std > 0, std, np.nan)
    return frame, label


def anomaly_method_text(frame, label, series_name):
    from workspace_app.social import decimal

    years = (frame.index.max() - frame.index.min()).days / 365.25
    return (
        f"Anomali pada deret {series_name} dihitung sebagai selisih nilai terhadap {label} dari data yang "
        f"sama ({decimal(years, 1)} tahun), dan skor z = anomali ÷ simpangan baku periode yang sama. Pembanding ini "
        f"adalah rata-rata rentang data yang ada, bukan normal iklim 30 tahun (WMO), sehingga anomali menggambarkan "
        f"penyimpangan dalam rentang ini; |z| ≥ 2 dianggap menonjol.")
