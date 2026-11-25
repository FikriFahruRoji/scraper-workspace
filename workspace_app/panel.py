"""Panel negara-tahun (World Bank, IMF, OECD): trend/CAGR, indicator correlation, country clustering,
and fixed-effects panel regression. Plain functions, no Streamlit."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from workspace_app import optional

COUNTRY_COLUMNS = ("negara", "country", "country_name", "negara_kode", "kode_negara")
YEAR_COLUMNS = ("tahun", "year", "periode", "period")
INDICATOR_COLUMNS = ("indikator", "indicator", "kode_indikator", "indicator_code", "dataflow")
VALUE_COLUMNS = ("nilai", "value", "obs_value")


class NotEnough(ValueError):
    """Raised when the dataset lacks panel columns or minimum observations for an analysis."""


@dataclass
class PreparedPanel:
    long: pd.DataFrame  # columns: negara, tahun, indikator, nilai
    wide: pd.DataFrame  # index: MultiIndex(negara, tahun), columns: indicators
    countries: list
    years: list
    indicators: list
    report: dict


def _first(frame, candidates):
    lowered = {str(col).lower(): col for col in frame}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def _clean_year(series):
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        return numeric
    text = series.astype(str).str.extract(r"(\d{4})", expand=False)
    return pd.to_numeric(text, errors="coerce")


def prepare(frame, country_col=None, year_col=None, indicator_col=None, value_col=None):
    """Normalise a country-year panel dataset into long and wide forms."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")
    country_col = country_col or _first(frame, COUNTRY_COLUMNS)
    year_col = year_col or _first(frame, YEAR_COLUMNS)
    value_col = value_col or _first(frame, VALUE_COLUMNS)
    if country_col is None or year_col is None or value_col is None:
        raise NotEnough("Dataset ini tidak punya kolom panel negara-tahun (negara, tahun, nilai). "
                        "Coba World Bank, IMF, atau OECD.")
    if indicator_col is None:
        indicator_col = _first(frame, INDICATOR_COLUMNS)

    total = len(frame)
    work = pd.DataFrame({
        "negara": frame[country_col].astype(str).str.strip(),
        "tahun": _clean_year(frame[year_col]),
        "nilai": pd.to_numeric(frame[value_col], errors="coerce"),
    })
    if indicator_col is not None and indicator_col in frame:
        work["indikator"] = frame[indicator_col].astype(str).str.strip()
        work.loc[work["indikator"].isin(["", "nan", "None"]), "indikator"] = str(value_col)
    else:
        work["indikator"] = str(value_col)

    valid_mask = (
        work["negara"].ne("")
        & ~work["negara"].isin(["nan", "None", "NaN"])
        & work["tahun"].notna()
        & work["nilai"].notna()
    )
    cleaned = work.loc[valid_mask].copy()
    if cleaned.empty:
        raise NotEnough("Tidak ada baris valid dengan negara, tahun, dan nilai angka.")
    cleaned["tahun"] = cleaned["tahun"].astype(int)

    # Aggregate duplicates (e.g. quarterly/monthly observations within the same country-year-indicator)
    grouped = (
        cleaned.groupby(["negara", "tahun", "indikator"], as_index=False)["nilai"]
        .mean()
        .sort_values(["indikator", "negara", "tahun"])
        .reset_index(drop=True)
    )
    wide = grouped.pivot(index=["negara", "tahun"], columns="indikator", values="nilai").sort_index()
    wide.columns.name = None

    countries = sorted(grouped["negara"].unique().tolist())
    years = sorted(grouped["tahun"].unique().tolist())
    indicators = sorted(grouped["indikator"].unique().tolist())
    report = {
        "baris": total,
        "dipakai": len(cleaned),
        "dibuang": total - len(cleaned),
        "negara": len(countries),
        "tahun": len(years),
        "indikator": len(indicators),
        "kolom_negara": str(country_col),
        "kolom_tahun": str(year_col),
        "kolom_indikator": str(indicator_col) if indicator_col else "",
        "kolom_nilai": str(value_col),
    }
    return PreparedPanel(long=grouped, wide=wide, countries=countries, years=years,
                         indicators=indicators, report=report)


# --------------------------------------------------------------------------- #
# 1. Trend, CAGR, and country comparison
# --------------------------------------------------------------------------- #

def trend_cagr(panel, indicator, countries=None):
    """Compute trend series and CAGR per country for one indicator."""
    sub = panel.long[panel.long["indikator"] == indicator].copy()
    if countries:
        sub = sub[sub["negara"].isin(countries)]
    if sub.empty:
        raise NotEnough("Tidak ada data untuk indikator dan negara yang dipilih.")
    years = sorted(sub["tahun"].unique().tolist())
    if len(years) < 2:
        raise NotEnough(f"Butuh minimal 2 tahun untuk tren dan CAGR, data ini hanya {len(years)} tahun.")

    rows = []
    for country, grp in sub.groupby("negara"):
        grp = grp.sort_values("tahun")
        first = grp.iloc[0]
        last = grp.iloc[-1]
        span = int(last["tahun"]) - int(first["tahun"])
        start_val = float(first["nilai"])
        end_val = float(last["nilai"])
        change = end_val - start_val
        pct = (change / start_val * 100.0) if start_val != 0 else np.nan
        if span > 0 and start_val > 0 and end_val > 0:
            cagr = ((end_val / start_val) ** (1.0 / span) - 1.0) * 100.0
        else:
            cagr = np.nan
        rows.append({
            "negara": country,
            "tahun_awal": int(first["tahun"]),
            "nilai_awal": start_val,
            "tahun_akhir": int(last["tahun"]),
            "nilai_akhir": end_val,
            "perubahan": change,
            "perubahan_persen": pct,
            "cagr_persen": cagr,
            "jumlah_tahun": len(grp),
        })
    summary = pd.DataFrame(rows).sort_values(["cagr_persen", "nilai_akhir"], ascending=[False, False], na_position="last")
    summary = summary.reset_index(drop=True)
    return {"series": sub.sort_values(["negara", "tahun"]).reset_index(drop=True),
            "summary": summary, "years": years}


def trend_method_text(origin, indicator, result):
    years = result["years"]
    n_countries = len(result["summary"])
    return (
        f"Tren dan laju pertumbuhan majemuk tahunan (CAGR) untuk indikator \"{indicator}\" pada {origin} "
        f"dihitung dengan pandas pada {n_countries} negara dalam rentang tahun {min(years)}–{max(years)} "
        f"({len(years)} titik tahun). CAGR dihitung sebagai ((nilai_akhir / nilai_awal)^(1 / Δtahun) − 1) × 100 % "
        f"untuk negara dengan nilai awal dan akhir positif serta minimal dua tahun pengamatan."
    )


# --------------------------------------------------------------------------- #
# 2. Indicator correlation
# --------------------------------------------------------------------------- #

def indicator_correlation(panel, indicators=None, method="pearson", year=None):
    """Pearson or Spearman correlation matrix and pairwise p-values across indicators."""
    cols = list(indicators) if indicators else list(panel.indicators)
    cols = [c for c in cols if c in panel.wide.columns]
    if len(cols) < 2:
        raise NotEnough(f"Butuh minimal 2 indikator untuk korelasi, dataset ini hanya {len(cols)}.")

    data = panel.wide[cols].copy()
    if year is not None:
        data = data.xs(int(year), level="tahun", drop_level=False)
    if len(data.dropna(how="all")) < 3:
        raise NotEnough("Butuh minimal 3 pasangan pengamatan untuk menghitung korelasi.")

    method_key = "spearman" if str(method).lower() == "spearman" else "pearson"
    corr_fn = stats.spearmanr if method_key == "spearman" else stats.pearsonr

    matrix = data.corr(method=method_key)
    pairs = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            c1, c2 = cols[i], cols[j]
            paired = data[[c1, c2]].dropna()
            n = len(paired)
            if n >= 3 and paired[c1].nunique() > 1 and paired[c2].nunique() > 1:
                r, p = corr_fn(paired[c1].to_numpy(), paired[c2].to_numpy())
                r, p = float(r), float(p)
            else:
                r, p = np.nan, np.nan
            pairs.append({
                "indikator_1": c1,
                "indikator_2": c2,
                "r": r,
                "p_value": p,
                "n": n,
            })
    pairs_df = pd.DataFrame(pairs).sort_values("r", key=lambda s: s.abs(), ascending=False, na_position="last")
    return {"matrix": matrix, "pairs": pairs_df.reset_index(drop=True), "method": method_key, "year": year}


def correlation_method_text(origin, result):
    label_method = "Spearman (peringkat)" if result["method"] == "spearman" else "Pearson"
    scope = f"pada tahun {result['year']}" if result["year"] is not None else "lintas seluruh pasangan negara-tahun"
    return (
        f"Korelasi antarindikator pada {origin} ({scope}) dihitung menggunakan koefisien {label_method} "
        f"berpasangan (pairwise complete observations) dengan uji signifikansi dua arah (SciPy)."
    )


# --------------------------------------------------------------------------- #
# 3. Country clustering (k-means)
# --------------------------------------------------------------------------- #

def cluster_countries(panel, indicators=None, year=None, aggregation="latest", n_clusters=3, seed=42):
    """K-means clustering on standardised country indicator vectors."""
    cols = list(indicators) if indicators else list(panel.indicators)
    cols = [c for c in cols if c in panel.wide.columns]
    if not cols:
        raise NotEnough("Pilih minimal 1 indikator untuk klaster negara.")

    sub = panel.wide[cols].copy()
    if year is not None:
        sub = sub.xs(int(year), level="tahun")
    elif aggregation == "mean":
        sub = sub.groupby(level="negara").mean()
    else:
        # Latest non-null value per country per indicator
        sub = sub.sort_index(level="tahun").groupby(level="negara").last()

    complete = sub.dropna(subset=cols).copy()
    n_countries = len(complete)
    if n_countries < 5:
        raise NotEnough(f"Butuh minimal 5 negara dengan data lengkap pada indikator terpilih, tersedia {n_countries} negara.")

    k = int(n_clusters)
    if k < 2 or k >= n_countries:
        raise NotEnough(f"Jumlah klaster harus antara 2 dan {n_countries - 1} untuk {n_countries} negara.")

    scaler = StandardScaler()
    scaled = scaler.fit_transform(complete[cols].to_numpy(dtype=float))
    model = KMeans(n_clusters=k, random_state=int(seed), n_init=10)
    labels = model.fit_predict(scaled)

    # Order cluster numbers deterministically by size (descending), then mean z-score
    order = (
        pd.Series(labels)
        .value_counts()
        .sort_values(ascending=False)
        .index.tolist()
    )
    remap = {old: idx + 1 for idx, old in enumerate(order)}
    ordered_labels = [remap[int(lbl)] for lbl in labels]

    assignments = complete.copy()
    assignments.insert(0, "klaster", ordered_labels)
    assignments = assignments.reset_index().sort_values(["klaster", "negara"]).reset_index(drop=True)

    centers = assignments.groupby("klaster")[cols].mean().reset_index()
    centers.insert(1, "jumlah_negara", assignments.groupby("klaster")["negara"].count().values)

    sil = float(silhouette_score(scaled, ordered_labels)) if 2 <= k < n_countries else np.nan
    return {
        "assignments": assignments,
        "centers": centers,
        "silhouette": sil,
        "inertia": float(model.inertia_),
        "n_countries": n_countries,
        "n_clusters": k,
        "indicators": cols,
        "year": year,
        "aggregation": aggregation,
        "seed": int(seed),
    }


def cluster_method_text(origin, result):
    basis = (
        f"tahun {result['year']}"
        if result["year"] is not None
        else ("rata-rata lintas tahun" if result["aggregation"] == "mean" else "nilai tahun terakhir tiap negara")
    )
    sil = f", koefisien silhouette {result['silhouette']:.3f}" if pd.notna(result["silhouette"]) else ""
    return (
        f"Pengelompokan {result['n_countries']} negara pada {origin} ({basis}) dilakukan dengan algoritma k-means "
        f"(scikit-learn, k = {result['n_clusters']}, n_init = 10, seed {result['seed']}) setelah standardisasi z-score "
        f"(StandardScaler) pada {len(result['indicators'])} indikator{sil}."
    )


# --------------------------------------------------------------------------- #
# 4. Fixed-effects panel regression (linearmodels)
# --------------------------------------------------------------------------- #

def panel_regression(panel, dependent, independents, entity_effects=True, time_effects=False):
    """Fixed-effects panel regression via linearmodels.PanelOLS."""
    if not optional.available("panel"):
        raise NotEnough(f"Regresi panel efek tetap membutuhkan paket linearmodels. Pasang dengan: {optional.INSTALL_COMMAND}")

    independents = [col for col in independents if col != dependent and col in panel.wide.columns]
    if dependent not in panel.wide.columns or not independents:
        raise NotEnough("Butuh minimal 2 indikator berbeda (1 dependen dan ≥ 1 independen).")

    cols = [dependent, *independents]
    data = panel.wide[cols].dropna().copy()
    if data.empty:
        raise NotEnough("Tidak ada baris lengkap tanpa nilai kosong untuk variabel yang dipilih.")

    n_entities = data.index.get_level_values("negara").nunique()
    n_years = data.index.get_level_values("tahun").nunique()
    if n_entities < 3:
        raise NotEnough(f"Butuh minimal 3 negara dengan data lengkap, tersedia {n_entities} negara.")
    if n_years < 2:
        raise NotEnough(f"Butuh minimal 2 tahun dengan data lengkap, tersedia {n_years} tahun.")
    min_df = len(independents) + (n_entities if entity_effects else 1) + (n_years - 1 if time_effects else 0)
    if len(data) <= min_df:
        raise NotEnough(f"Jumlah pengamatan ({len(data)}) terlalu sedikit untuk jumlah parameter dan efek tetap ({min_df}).")

    from linearmodels.panel import PanelOLS

    y = data[dependent]
    X = data[independents]
    if not entity_effects and not time_effects:
        X = X.copy()
        X.insert(0, "konstanta", 1.0)

    model = PanelOLS(y, X, entity_effects=bool(entity_effects), time_effects=bool(time_effects), drop_absorbed=True)
    cov_used = "clustered" if entity_effects else "robust"
    try:
        fitted = model.fit(cov_type="clustered", cluster_entity=True) if entity_effects else model.fit(cov_type="robust")
    except Exception:
        cov_used = "robust"
        fitted = model.fit(cov_type="robust")

    ci = fitted.conf_int()
    coef_table = pd.DataFrame({
        "variabel": fitted.params.index.tolist(),
        "koefisien": fitted.params.values,
        "galat_baku": fitted.std_errors.values,
        "t_stat": fitted.tstats.values,
        "p_value": fitted.pvalues.values,
        "ci_bawah": ci["lower"].values,
        "ci_atas": ci["upper"].values,
    })

    return {
        "coefficients": coef_table,
        "r2_within": float(fitted.rsquared_within),
        "r2_between": float(fitted.rsquared_between),
        "r2_overall": float(fitted.rsquared_overall),
        "f_stat": float(fitted.f_statistic.stat) if fitted.f_statistic is not None else np.nan,
        "f_pvalue": float(fitted.f_statistic.pval) if fitted.f_statistic is not None else np.nan,
        "n_obs": int(fitted.nobs),
        "n_countries": int(n_entities),
        "n_years": int(n_years),
        "entity_effects": bool(entity_effects),
        "time_effects": bool(time_effects),
        "cov_type": cov_used,
        "dependent": dependent,
        "independents": independents,
    }


def regression_method_text(origin, result):
    effects = []
    if result["entity_effects"]:
        effects.append("efek tetap negara (entity fixed effects)")
    if result["time_effects"]:
        effects.append("efek tetap tahun (time fixed effects)")
    effects_txt = " dan ".join(effects) if effects else "regresi panel gabungan (pooled OLS)"
    cov_txt = "galat baku terklaster per negara (clustered SE)" if result["cov_type"] == "clustered" else "galat baku robust (HC)"
    indep = ", ".join(result["independents"])
    return (
        f"Regresi panel pada {origin} mengestimasi pengaruh {indep} terhadap {result['dependent']} "
        f"menggunakan PanelOLS (linearmodels) dengan {effects_txt} dan {cov_txt} "
        f"(N = {result['n_obs']} pengamatan, {result['n_countries']} negara, {result['n_years']} tahun; "
        f"R² within = {result['r2_within']:.4f})."
    )
