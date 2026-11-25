"""Lintas dataset: align two datasets by date (and optional region), compute lag correlations,
and run Granger causality tests (statsmodels). Plain functions, no Streamlit."""
import numpy as np
import pandas as pd
from scipy import stats

from workspace_app import analysis as an
from workspace_app import optional

COUNT_ROWS = "__hitung__"
NET_SENTIMENT = "__sentimen_bersih__"
SENTIMENT_SCORE = {"positif": 1.0, "positive": 1.0, "netral": 0.0, "neutral": 0.0, "negatif": -1.0, "negative": -1.0}
REGION_COLUMNS = ("provinsi", "lokasi", "kota", "wilayah", "negara")


class NotEnough(ValueError):
    """Raised when two datasets cannot be aligned or lack enough overlapping points."""


def region_columns(frame):
    """Candidate region columns present in `frame`."""
    lowered = {str(col).lower(): col for col in frame}
    return [lowered[name] for name in REGION_COLUMNS if name in lowered]


def extract_series(frame, date_col=None, value_col=COUNT_ROWS, region_col=None, region_value=None,
                   freq="D", agg="mean"):
    """Extract a periodic numeric series from a dataset, optionally filtered by region."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")

    dates = an.date_columns(frame)
    date_col = date_col or (dates[0] if dates else None)
    if date_col is None or date_col not in frame:
        raise NotEnough("Dataset tidak punya kolom tanggal untuk diselaraskan.")

    work = frame.copy()
    if region_col and region_value and region_col in work:
        work = work[work[region_col].astype(str) == str(region_value)].copy()
        if work.empty:
            raise NotEnough(f"Tidak ada baris untuk wilayah `{region_value}`.")

    parsed = pd.to_datetime(work[date_col], errors="coerce", utc=True)
    if parsed.notna().sum() == 0:
        parsed = pd.to_datetime(work[date_col], errors="coerce")
    else:
        parsed = parsed.dt.tz_convert("Asia/Jakarta").dt.tz_localize(None)

    valid = parsed.notna()
    work = work.loc[valid].copy()
    if work.empty:
        raise NotEnough("Tidak ada tanggal valid pada kolom tanggal yang dipilih.")

    work["_period"] = parsed.loc[valid].dt.to_period(freq).dt.to_timestamp()

    if value_col == COUNT_ROWS:
        series = work.groupby("_period").size().astype(float)
    elif value_col == NET_SENTIMENT:
        sent_col = next((c for c in work.columns if str(c).lower() == "sentimen"), None)
        if sent_col is None:
            raise NotEnough("Dataset ini tidak punya kolom `sentimen`.")
        scores = work[sent_col].astype(str).str.strip().str.lower().map(SENTIMENT_SCORE)
        work["_val"] = scores
        series = work.dropna(subset=["_val"]).groupby("_period")["_val"].mean()
    else:
        if value_col not in work:
            raise NotEnough(f"Kolom `{value_col}` tidak ditemukan.")
        work["_val"] = pd.to_numeric(work[value_col], errors="coerce")
        cleaned = work.dropna(subset=["_val"])
        if cleaned.empty:
            raise NotEnough(f"Kolom `{value_col}` tidak punya nilai angka valid.")
        agg_fn = agg if agg in ("mean", "sum", "median", "max", "last") else "mean"
        series = getattr(cleaned.groupby("_period")["_val"], agg_fn)()

    series = series.sort_index().dropna()
    if series.empty:
        raise NotEnough("Deret hasil agregasi kosong.")
    return series


def align_series(series_a, series_b, name_a="A", name_b="B", freq="D", fill="drop"):
    """Align two periodic series on overlapping dates."""
    if series_a.empty or series_b.empty:
        raise NotEnough("Salah satu deret kosong.")

    s_a = series_a.resample(freq).mean() if freq else series_a.copy()
    s_b = series_b.resample(freq).mean() if freq else series_b.copy()
    merged = pd.DataFrame({name_a: s_a, name_b: s_b}).sort_index()
    if fill == "ffill":
        merged = merged.ffill().dropna()
    else:
        merged = merged.dropna()

    if len(merged) < 15:
        raise NotEnough(
            f"Titik tanggal yang beririsan terlalu sedikit ({len(merged)} titik). "
            "Butuh minimal 15 titik beririsan (coba ubah frekuensi ke mingguan atau bulanan)."
        )
    merged.index.name = "tanggal"
    return {
        "aligned": merged,
        "name_a": name_a,
        "name_b": name_b,
        "n_points": len(merged),
        "start": merged.index.min(),
        "end": merged.index.max(),
    }


def lag_correlation(aligned_res, max_lag=7, method="pearson"):
    """Compute cross-correlation for lags -max_lag .. +max_lag.
    Positive lag k means A(t - k) -> B(t) (A leads B). Negative lag k means B leads A."""
    df = aligned_res["aligned"]
    a_col, b_col = aligned_res["name_a"], aligned_res["name_b"]
    a, b = df[a_col], df[b_col]

    method_key = "spearman" if str(method).lower() == "spearman" else "pearson"
    corr_fn = stats.spearmanr if method_key == "spearman" else stats.pearsonr

    max_k = min(int(max_lag), max(1, len(df) // 3))
    rows = []
    for k in range(-max_k, max_k + 1):
        shifted_a = a.shift(k)  # A(t-k) paired with B(t)
        paired = pd.DataFrame({"a": shifted_a, "b": b}).dropna()
        n = len(paired)
        if n >= 5 and paired["a"].nunique() > 1 and paired["b"].nunique() > 1:
            r, p = corr_fn(paired["a"].to_numpy(), paired["b"].to_numpy())
            r, p = float(r), float(p)
        else:
            r, p = np.nan, np.nan

        if k > 0:
            direction = f"{a_col} mendahului {b_col} ({k} periode)"
        elif k < 0:
            direction = f"{b_col} mendahului {a_col} ({abs(k)} periode)"
        else:
            direction = "Sezaman (lag 0)"

        rows.append({"lag": k, "arah": direction, "r": r, "p_value": p, "n": n})

    lags_df = pd.DataFrame(rows)
    valid = lags_df.dropna(subset=["r"])
    best = valid.loc[valid["r"].abs().idxmax()].to_dict() if not valid.empty else None
    return {"lags": lags_df, "best": best, "method": method_key, "max_lag": max_k}


def granger_test(aligned_res, max_lag=4, difference=True):
    """Run bidirectional Granger causality tests (A -> B and B -> A) via statsmodels."""
    if not optional.available("cross"):
        raise NotEnough(f"Uji kausalitas Granger membutuhkan statsmodels. Pasang dengan: {optional.INSTALL_COMMAND}")

    from statsmodels.tsa.stattools import grangercausalitytests

    df = aligned_res["aligned"].copy()
    a_col, b_col = aligned_res["name_a"], aligned_res["name_b"]
    if difference:
        df = df.diff().dropna()

    k = int(max_lag)
    min_obs = 3 * k + 6
    if len(df) < min_obs:
        raise NotEnough(f"Butuh minimal {min_obs} titik pengamatan untuk lag {k}, tersedia {len(df)}.")
    if df[a_col].nunique() <= 1 or df[b_col].nunique() <= 1:
        raise NotEnough("Salah satu deret bernilai konstan setelah penyelarasan/diferensiasi.")

    results = []
    for direction, target, cause in (
        (f"{a_col} → {b_col}", b_col, a_col),
        (f"{b_col} → {a_col}", a_col, b_col),
    ):
        pair = df[[target, cause]].to_numpy(dtype=float)
        raw = grangercausalitytests(pair, maxlag=k)
        for lag_i in range(1, k + 1):
            f_stat, p_val, _, _ = raw[lag_i][0]["ssr_ftest"]
            results.append({
                "arah": direction,
                "lag": lag_i,
                "f_stat": float(f_stat),
                "p_value": float(p_val),
                "signifikan_5pct": bool(p_val < 0.05),
            })

    res_df = pd.DataFrame(results)
    return {"results": res_df, "differenced": bool(difference), "max_lag": k, "n_obs": len(df)}


def cross_method_text(aligned_res, lag_res, granger_res=None):
    a_col, b_col = aligned_res["name_a"], aligned_res["name_b"]
    method_label = "Spearman" if lag_res["method"] == "spearman" else "Pearson"
    best = lag_res["best"]
    best_txt = (
        f" Korelasi terkuat berada pada lag {best['lag']} ({best['arah']}, r = {best['r']:.3f}, "
        f"p = {best['p_value']:.4f})."
        if best
        else ""
    )
    granger_txt = ""
    if granger_res is not None:
        diff_txt = "pada selisih pertama (first difference)" if granger_res["differenced"] else "pada level"
        granger_txt = (
            f" Uji kausalitas Granger (statsmodels) dijalankan dua arah hingga lag {granger_res['max_lag']} "
            f"{diff_txt} (N = {granger_res['n_obs']})."
        )
    return (
        f"Dua deret ({a_col} dan {b_col}) diselaraskan per tanggal pada {aligned_res['n_points']} titik irisan, "
        f"lalu dianalisis dengan korelasi berlag {method_label} (±{lag_res['max_lag']} periode).{best_txt}{granger_txt} "
        f"Catatan: korelasi dan uji Granger mengukur keterkaitan/prediksi berurutan waktu, bukan bukti hubungan sebab akibat."
    )
