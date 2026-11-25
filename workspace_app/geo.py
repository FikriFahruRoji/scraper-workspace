"""Analyses for geospatial events (family F in docs/ANALISIS.md): plain functions, no Streamlit.

- `coordinates` reads latitude and longitude columns and drops what cannot be a place;
- `assign_regions` puts points in kabupaten/kota (the same outlines and rules as the Bencana scraper, written
  with numpy so 100 thousand points take seconds);
- `cluster_points` finds dense groups with HDBSCAN on great-circle distance;
- `gutenberg_richter` and `depth_summary` describe earthquakes.
"""
from functools import lru_cache
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

AREAS_FILE = Path(__file__).resolve().parents[1] / "scrapers" / "bencana" / "wilayah_idn.json"
KM_PER_DEGREE = 111.32  # same constant as the Bencana scraper
NEAR_KM = 20  # a point off the coast takes the nearest kabupaten within this distance
EARTH_KM = 6371.0
DENSIFY_KM = 1.0  # outline edges are subdivided to this length before the nearest-edge search
LAT_COLUMNS = ("lintang", "latitude", "lat")
LON_COLUMNS = ("bujur", "longitude", "lon", "lng")
MAGNITUDE_COLUMNS = ("magnitudo", "magnitude", "mag")
DEPTH_COLUMNS = ("kedalaman_km", "depth", "depth_km")
SEED = 42
MIN_CLUSTER_POINTS = 30
MAX_CLUSTER_POINTS = 15_000  # HDBSCAN on more than this is sampled (seed 42): 10k points took 3 s, 20k took 16 s, 30k 26 s
MAX_MAP_POINTS = 200_000
MIN_QUAKES = 50
MC_CORRECTION = 0.2  # Woessner & Wiemer (2005): max curvature underestimates the completeness magnitude
DEPTH_CLASSES = (("dangkal (< 70 km)", 0, 70), ("menengah (70–300 km)", 70, 300), ("dalam (> 300 km)", 300, 1e9))


class NotEnough(ValueError):
    """The data does not meet a requirement of the analysis; the message says which."""


def find_column(frame, names):
    lowered = {str(column).lower(): column for column in frame}
    return next((lowered[name] for name in names if name in lowered), None)


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #

def coordinates(frame):
    """(frame of the rows with a usable place, report). Adds numeric columns `_lat` and `_lon`.

    Dropped: missing or non-numeric values, latitude outside ±90, longitude outside ±180, and exactly (0, 0),
    which scrapers write when a place is unknown."""
    lat_name, lon_name = find_column(frame, LAT_COLUMNS), find_column(frame, LON_COLUMNS)
    if lat_name is None or lon_name is None:
        raise NotEnough("Dataset ini tidak punya kolom lintang dan bujur.")
    lat = pd.to_numeric(frame[lat_name], errors="coerce")
    lon = pd.to_numeric(frame[lon_name], errors="coerce")
    missing = lat.isna() | lon.isna()
    out_of_range = ~missing & ((lat.abs() > 90) | (lon.abs() > 180))
    zero = ~missing & ~out_of_range & (lat == 0) & (lon == 0)
    keep = ~(missing | out_of_range | zero)
    found = frame.loc[keep].copy()
    found["_lat"], found["_lon"] = lat[keep].astype(float), lon[keep].astype(float)
    if found.empty:
        raise NotEnough("Tidak ada baris dengan koordinat yang valid.")
    report = {"baris": len(frame), "dipakai": int(keep.sum()), "kosong": int(missing.sum()),
              "di_luar_jangkauan": int(out_of_range.sum()), "nol_nol": int(zero.sum()),
              "kolom_lintang": lat_name, "kolom_bujur": lon_name}
    return found, report


def bounds(lat, lon):
    return {"selatan": float(lat.min()), "utara": float(lat.max()), "barat": float(lon.min()),
            "timur": float(lon.max())}


def density_method_text(report, origin, radius_km, weight=None):
    from workspace_app.social import decimal

    shown = f"{report['dipakai']} dari {report['baris']} baris" if report["dipakai"] != report["baris"] \
        else f"{report['baris']} baris"
    dropped = [f"{report[key]} {label}" for key, label in (("kosong", "tanpa koordinat"),
                                                           ("di_luar_jangkauan", "dengan koordinat di luar jangkauan"),
                                                           ("nol_nol", "dengan koordinat tepat (0, 0)")) if report[key]]
    return (
        f"Sebaran titik {origin} ({shown} dengan koordinat valid" + (f"; dibuang: {', '.join(dropped)}" if dropped else "")
        + f") dipetakan dengan agregasi heksagon (HexagonLayer, pydeck/deck.gl) berjari-jari {decimal(radius_km, 1)} km; "
        f"warna menunjukkan " + (f"jumlah nilai {weight} di tiap heksagon" if weight else "jumlah titik di tiap heksagon")
        + ". Peta dasar CARTO dimuat dari internet; agregasi dilakukan di peramban. Kepadatan titik mencerminkan "
        "sebaran kejadian sekaligus sebaran pengamatan (wilayah yang lebih diawasi atau lebih padat penduduk tercatat "
        "lebih banyak), sehingga bukan ukuran risiko.")


# --------------------------------------------------------------------------- #
# Kabupaten and kota
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def areas():
    """The outlines: list of dicts with name, province, bbox and edge arrays (x1, y1, x2, y2), loaded once."""
    data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    found = []
    for item in data["areas"]:
        x1, y1, x2, y2 = [], [], [], []
        for flat in item["r"]:
            xs, ys = np.asarray(flat[0::2], float), np.asarray(flat[1::2], float)
            x1.append(xs), y1.append(ys), x2.append(np.roll(xs, -1)), y2.append(np.roll(ys, -1))
        found.append({"nama": item["k"], "provinsi": item["p"], "bbox": tuple(item["b"]),
                      "x1": np.concatenate(x1), "y1": np.concatenate(y1),
                      "x2": np.concatenate(x2), "y2": np.concatenate(y2)})
    return found


def _unit_vectors(lat, lon):
    lat_r, lon_r = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(lat_r) * np.cos(lon_r), np.cos(lat_r) * np.sin(lon_r), np.sin(lat_r)])


@lru_cache(maxsize=1)
def _outline_tree():
    """KD-tree over outline points (edges subdivided to DENSIFY_KM): chord distance × EARTH_KM ≈ km."""
    from scipy.spatial import cKDTree

    lat, lon, owner = [], [], []
    for number, area in enumerate(areas()):
        steps = np.ceil(np.hypot((area["x2"] - area["x1"]) * math.cos(math.radians(float(np.mean(area["y1"])))),
                                 area["y2"] - area["y1"]) * KM_PER_DEGREE / DENSIFY_KM).astype(int)
        for x1, y1, x2, y2, count in zip(area["x1"], area["y1"], area["x2"], area["y2"], steps):
            fractions = np.linspace(0, 1, max(count, 1), endpoint=False)
            lon.append(x1 + (x2 - x1) * fractions), lat.append(y1 + (y2 - y1) * fractions)
            owner.append(np.full(len(fractions), number))
    lat, lon, owner = np.concatenate(lat), np.concatenate(lon), np.concatenate(owner)
    return cKDTree(_unit_vectors(lat, lon)), owner


def _inside(x, y, area, budget=4_000_000):
    """Even-odd rule (holes and separate islands included) for the points x, y against one area's edges."""
    edges = len(area["x1"])
    result = np.zeros(len(x), bool)
    step = max(1, budget // max(edges, 1))
    x1, y1, x2, y2 = area["x1"], area["y1"], area["x2"], area["y2"]
    for start in range(0, len(x), step):
        px, py = x[start:start + step, None], y[start:start + step, None]
        straddle = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            crossing = px < x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        result[start:start + step] = (straddle & crossing).sum(axis=1) % 2 == 1
    return result


def assign_regions(lat, lon):
    """Kabupaten/kota and province of each point; same rule as the Bencana scraper.

    A point inside an outline gets that kabupaten (distance 0). A point outside every outline takes the nearest
    kabupaten within NEAR_KM (the distance is to the nearest outline point, accurate to about 1 km). Others get
    "". Returns a DataFrame with kabupaten_kota, provinsi, jarak_wilayah_km (NaN when unassigned)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    count = len(lat)
    owner = np.full(count, -1)
    distance = np.full(count, np.nan)
    listed = areas()
    for number, area in enumerate(listed):
        west, south, east, north = area["bbox"]
        candidates = np.flatnonzero((owner < 0) & (lon >= west) & (lon <= east) & (lat >= south) & (lat <= north))
        if len(candidates):
            hit = candidates[_inside(lon[candidates], lat[candidates], area)]
            owner[hit], distance[hit] = number, 0.0
    outside = np.flatnonzero(owner < 0)
    if len(outside):
        tree, owners = _outline_tree()
        chord, nearest = tree.query(_unit_vectors(lat[outside], lon[outside]), distance_upper_bound=NEAR_KM / EARTH_KM)
        near = np.isfinite(chord)
        owner[outside[near]] = owners[nearest[near]]
        # at least 0.1 so that 0 only ever means inside
        distance[outside[near]] = np.maximum(np.round(2 * EARTH_KM * np.arcsin(np.minimum(chord[near] / 2, 1)), 1), 0.1)
    names = np.array([area["nama"] for area in listed] + [""])
    provinces = np.array([area["provinsi"] for area in listed] + [""])
    return pd.DataFrame({"kabupaten_kota": names[owner], "provinsi": provinces[owner], "jarak_wilayah_km": distance})


def region_table(frame, level="kabupaten_kota", weight=None):
    """Counts per kabupaten/kota or province (from columns added by `assign_regions`), most events first.

    With `weight` (a numeric column) also the sum and mean of it per area. Points outside Indonesia are
    left out and counted in the summary."""
    placed = frame[frame[level].astype(str).ne("")]
    if placed.empty:
        return pd.DataFrame(), {"total": len(frame), "di_wilayah": 0, "di_laut": 0, "luar": len(frame)}
    grouped = placed.groupby(level)
    table = grouped.size().rename("Kejadian").to_frame()
    if level == "kabupaten_kota":
        table["Provinsi"] = grouped["provinsi"].first()
    if weight is not None:
        values = pd.to_numeric(placed[weight], errors="coerce")
        table[f"Jumlah {weight}"] = values.groupby(placed[level]).sum()
        table[f"Rata-rata {weight}"] = values.groupby(placed[level]).mean()
    table["Porsi (%)"] = table["Kejadian"] / len(placed) * 100
    table = table.sort_values("Kejadian", ascending=False).reset_index().rename(
        columns={level: "Kabupaten/kota" if level == "kabupaten_kota" else "Provinsi"})
    inside = int((placed["jarak_wilayah_km"] == 0).sum())
    return table, {"total": len(frame), "di_wilayah": inside, "di_laut": len(placed) - inside,
                   "luar": len(frame) - len(placed)}


def region_method_text(summary, origin, level="kabupaten/kota"):
    from workspace_app.social import decimal

    return (
        f"Titik {origin} ditempatkan pada {level} secara luring dengan uji titik-dalam-poligon (aturan genap-ganjil, "
        f"pulau dan lubang diperhitungkan) terhadap garis batas kabupaten/kota yang disederhanakan dari geoBoundaries "
        f"IDN ADM2 (BPS/OCHA 2020, CC BY 3.0 IGO) dengan provinsi menurut OCHA COD-AB dan pemekaran 2022. Titik di luar "
        f"semua batas (misalnya episenter di laut) diberi kabupaten/kota terdekat bila jaraknya ≤ {NEAR_KM} km dari "
        f"garis batas (akurat sekitar 1 km). Dari {summary['total']} titik, {summary['di_wilayah']} berada di dalam "
        f"batas, {summary['di_laut']} di sekitar pantai, dan {summary['luar']} "
        f"({decimal(summary['luar'] / max(summary['total'], 1) * 100, 1)}%) di luar jangkauan sehingga tidak "
        f"dihitung. Garis batas disederhanakan, jadi titik tepat di perbatasan dapat tertukar ke kabupaten sebelah.")


# --------------------------------------------------------------------------- #
# Clusters
# --------------------------------------------------------------------------- #

def cluster_points(lat, lon, min_cluster_size=MIN_CLUSTER_POINTS, min_samples=None, limit=MAX_CLUSTER_POINTS):
    """HDBSCAN on great-circle (haversine) distance (Campello, Moulavi & Sander, 2013; scikit-learn).

    More than `limit` points are sampled without replacement (seed 42) and only the sample is clustered.
    Returns (labels aligned with the input, -1 = noise and -2 = not sampled; table of clusters; summary)."""
    from sklearn.cluster import HDBSCAN

    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if len(lat) < MIN_CLUSTER_POINTS:
        raise NotEnough(f"Klaster butuh minimal {MIN_CLUSTER_POINTS} titik, data ini {len(lat)}.")
    if min_cluster_size < 2:
        raise NotEnough("Ukuran klaster minimal harus 2 atau lebih.")
    chosen = np.arange(len(lat))
    if len(lat) > limit:
        chosen = np.sort(np.random.default_rng(SEED).choice(len(lat), limit, replace=False))
    model = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="haversine",
                    cluster_selection_method="eom", copy=True)
    sample = model.fit_predict(np.radians(np.column_stack([lat[chosen], lon[chosen]])))
    labels = np.full(len(lat), -2)
    labels[chosen] = sample
    vectors = _unit_vectors(lat[chosen], lon[chosen])
    rows = []
    for number in sorted(set(sample) - {-1}):
        members = sample == number
        centre = vectors[members].mean(axis=0)
        centre /= np.linalg.norm(centre)
        radius = np.arccos(np.clip(vectors[members] @ centre, -1, 1)) * EARTH_KM
        rows.append({"Klaster": int(number), "Titik": int(members.sum()),
                     "Lintang pusat": float(np.degrees(np.arcsin(centre[2]))),
                     "Bujur pusat": float(np.degrees(np.arctan2(centre[1], centre[0]))),
                     "Radius median (km)": float(np.median(radius)), "Radius 90% (km)": float(np.percentile(radius, 90))})
    table = pd.DataFrame(rows).sort_values("Titik", ascending=False, ignore_index=True) if rows else pd.DataFrame(
        columns=["Klaster", "Titik", "Lintang pusat", "Bujur pusat", "Radius median (km)", "Radius 90% (km)"])
    summary = {"titik": len(lat), "dikelompokkan": len(chosen), "diambil_sampel": len(lat) > len(chosen),
               "klaster": len(rows), "noise": int((sample == -1).sum()),
               "min_cluster_size": min_cluster_size, "min_samples": min_samples, "seed": SEED}
    return labels, table, summary


def cluster_method_text(summary, origin):
    from workspace_app.social import decimal

    sampled = (f" Karena jumlah titik melebihi {MAX_CLUSTER_POINTS}, {summary['dikelompokkan']} titik dipilih acak "
               f"(seed {summary['seed']}) untuk dikelompokkan." if summary["diambil_sampel"] else "")
    share = summary["noise"] / max(summary["dikelompokkan"], 1) * 100
    return (
        f"Titik {origin} dikelompokkan dengan HDBSCAN (Campello, Moulavi, & Sander, 2013; scikit-learn) memakai jarak "
        f"lingkaran besar (haversine) pada koordinat dalam radian, ukuran klaster minimal {summary['min_cluster_size']}, "
        f"min_samples {summary['min_samples'] if summary['min_samples'] else 'sama dengan ukuran klaster'}, dan pemilihan "
        f"klaster 'excess of mass'.{sampled} Hasilnya {summary['klaster']} klaster dan {summary['noise']} titik "
        f"({decimal(share, 1)}%) dianggap derau, yaitu tidak cukup padat untuk masuk klaster mana pun. Klaster adalah "
        f"kepadatan titik, bukan sebab: pola pengambilan data (misalnya satu sensor atau wilayah yang lebih diawasi) "
        f"dapat membentuk klaster.")


# --------------------------------------------------------------------------- #
# Earthquakes
# --------------------------------------------------------------------------- #

def completeness_magnitude(magnitudes, bin_width=0.1):
    """Magnitude of completeness by maximum curvature (Wiemer & Wyss, 2000) plus the usual +0.2 correction."""
    binned = np.round(np.asarray(magnitudes, float) / bin_width) * bin_width
    values, counts = np.unique(np.round(binned, 6), return_counts=True)
    return float(np.round(values[counts.argmax()] + MC_CORRECTION, 6))


def gutenberg_richter(magnitudes, bin_width=0.1, mc=None):
    """Gutenberg–Richter law log10 N(≥M) = a − bM for the quakes with magnitude ≥ Mc.

    b by the Aki (1965) maximum-likelihood estimator with Utsu's bin correction, b = log10(e) / (mean(M) −
    (Mc − ΔM/2)); its standard error is Shi & Bolt's (1982). Mc comes from `completeness_magnitude` unless given.
    Returns a dict with the estimates and the curves; b is None when fewer than MIN_QUAKES quakes reach Mc."""
    values = pd.to_numeric(pd.Series(magnitudes), errors="coerce").dropna().to_numpy(float)
    if len(values) < MIN_QUAKES:
        raise NotEnough(f"Gutenberg–Richter butuh minimal {MIN_QUAKES} gempa dengan magnitudo, data ini {len(values)}.")
    mc = completeness_magnitude(values, bin_width) if mc is None else float(mc)
    above = values[values >= mc - 1e-9]
    grid = np.round(np.arange(math.floor(values.min() / bin_width) * bin_width, values.max() + bin_width, bin_width), 6)
    cumulative = np.array([(values >= magnitude - 1e-9).sum() for magnitude in grid])
    counts = np.array([((values >= low - 1e-9) & (values < low + bin_width - 1e-9)).sum() for low in grid])
    result = {"n": len(values), "mc": mc, "n_di_atas_mc": len(above), "bin": bin_width, "m_min": float(values.min()),
              "m_maks": float(values.max()), "b": None, "sigma_b": None, "a": None,
              "kurva": pd.DataFrame({"Magnitudo": grid, "Per kelas": counts, "Kumulatif (≥ M)": cumulative})}
    if len(above) >= MIN_QUAKES:
        mean = float(above.mean())
        b = math.log10(math.e) / (mean - (mc - bin_width / 2))
        spread = float(((above - mean) ** 2).sum())
        sigma = 2.3 * b ** 2 * math.sqrt(spread / (len(above) * (len(above) - 1)))
        result.update(b=b, sigma_b=sigma, a=math.log10(len(above)) + b * mc)
    return result


def gutenberg_richter_text(result, origin):
    from workspace_app.social import decimal

    text = (
        f"Frekuensi–magnitudo gempa {origin} ({result['n']} gempa, magnitudo {decimal(result['m_min'], 1)}–"
        f"{decimal(result['m_maks'], 1)}) dianalisis dengan hukum Gutenberg–Richter, log₁₀ N(≥M) = a − bM "
        f"(Gutenberg & Richter, 1944). Magnitudo kelengkapan Mc = {decimal(result['mc'], 1)} ditentukan dengan metode "
        f"kurvatur maksimum (Wiemer & Wyss, 2000) ditambah koreksi 0,2 (Woessner & Wiemer, 2005); hanya "
        f"{result['n_di_atas_mc']} gempa dengan M ≥ Mc yang dipakai.")
    if result["b"] is None:
        return text + (f" Nilai b tidak dihitung karena hanya {result['n_di_atas_mc']} gempa di atas Mc "
                       f"(minimal {MIN_QUAKES}).")
    low, high = result["b"] - 1.96 * result["sigma_b"], result["b"] + 1.96 * result["sigma_b"]
    return text + (
        f" Nilai b diestimasi dengan penaksir kemungkinan maksimum Aki (1965) dengan koreksi lebar kelas Utsu "
        f"({decimal(result['bin'], 1)}): b = {decimal(result['b'], 2)} ± {decimal(result['sigma_b'], 2)} (galat baku "
        f"Shi & Bolt, 1982; selang 95% {decimal(low, 2)}–{decimal(high, 2)}), a = {decimal(result['a'], 2)}. "
        f"Nilai b sekitar 1 umum untuk kerak bumi; b yang lebih kecil berarti gempa besar relatif lebih banyak. "
        f"Estimasi bergantung pada Mc, dan katalog gabungan beberapa wilayah dapat mencampur kondisi tektonik yang berbeda.")


def depth_summary(depths):
    """Share of quakes per depth class (shallow < 70 km, intermediate 70–300, deep > 300) and basic statistics."""
    values = pd.to_numeric(pd.Series(depths), errors="coerce").dropna()
    if values.empty:
        raise NotEnough("Tidak ada nilai kedalaman yang terbaca.")
    rows = [{"Kelas": name, "Gempa": int(((values >= low) & (values < high)).sum())} for name, low, high in DEPTH_CLASSES]
    table = pd.DataFrame(rows)
    table["Persen"] = table["Gempa"] / len(values) * 100
    return table, {"median": float(values.median()), "persentil_90": float(values.quantile(0.9)),
                   "maks": float(values.max()), "n": len(values)}
