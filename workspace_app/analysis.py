"""Analyses that run locally on a saved dataset: plain functions, no Streamlit.

Text analyses work on cleaned text. A dataset that went through Preprocessing
already has `teks_bersih`; anything else is cleaned here with the word-analysis
preset (stopwords removed, no stemming, so it stays fast).
"""
from collections import Counter
from itertools import combinations
import re

import numpy as np
import pandas as pd

from workspace_app import preprocess as pp

CLEAN_COLUMN = "teks_bersih"
DATE_HINTS = ("tanggal", "waktu", "date", "time", "dimulai", "published", "created", "diterbitkan", "tgl", "tahun", "year")
MAX_DOCUMENTS = 20_000
PERIODS = {"D": "Harian", "W": "Mingguan", "ME": "Bulanan"}


# --------------------------------------------------------------------------- #
# Column discovery
# --------------------------------------------------------------------------- #

def text_columns(frame):
    """Columns that hold text: mostly strings, at least a few words on average."""
    found = []
    for column in frame:
        values = frame[column].dropna()
        if values.empty or not values.map(lambda value: isinstance(value, str)).mean() > 0.8:
            continue
        if values.astype(str).str.split().str.len().mean() >= 3 or column == CLEAN_COLUMN:
            found.append(column)
    return found


def default_text_columns(candidates):
    if CLEAN_COLUMN in candidates:
        return [CLEAN_COLUMN]
    return candidates[:1]


ZONE = "Asia/Jakarta"
OFFSET_RE = r"(?:Z|[+-]\d{2}:?\d{2})\s*$"
NUMBER_TEXT_RE = r"\s*[+-]?\d+(?:[.,]\d+)?\s*"  # numbers stored as text: not dates unless the column says so


def parse_dates(series):
    """Parse a column to naive datetimes; unparseable values become NaT.

    Values with a UTC offset are converted to WIB (Asia/Jakarta), the zone the
    scrapers report in. Values without an offset are kept exactly as written.
    """
    text = series.astype(str)
    has_zone = text.str.contains(OFFSET_RE, regex=True)
    zoned = pd.to_datetime(text[has_zone], errors="coerce", utc=True, format="mixed")
    zoned = zoned.dt.tz_convert(ZONE).dt.tz_localize(None)
    naive = pd.to_datetime(text[~has_zone], errors="coerce", format="mixed")
    parsed = pd.concat([zoned, naive]).reindex(series.index)
    # Numbers such as a latitude of "-6.2" can parse to years far outside what a nanosecond timestamp holds.
    parsed = parsed.where(parsed.dt.year.between(1800, 2200))
    return parsed.astype("datetime64[ns]")


def date_columns(frame):
    """Columns whose values mostly parse as dates, hinted names first."""
    found = []
    for column in frame:
        values = frame[column].dropna()
        if values.empty or pd.api.types.is_bool_dtype(values):
            continue
        sample = values.head(200)
        hinted = any(hint in column.lower() for hint in DATE_HINTS)
        if not hinted and (pd.api.types.is_numeric_dtype(sample)
                           or sample.astype(str).str.fullmatch(NUMBER_TEXT_RE).mean() >= 0.8):
            continue
        if parse_dates(sample).notna().mean() >= 0.8:
            found.append(column)
    return sorted(found, key=lambda name: not any(hint in name.lower() for hint in DATE_HINTS))


# --------------------------------------------------------------------------- #
# Statistik data
# --------------------------------------------------------------------------- #

def column_profile(frame):
    """One row per column: type, completeness, distinct values, and an example."""
    rows = []
    for column in frame:
        values = frame[column]
        filled = values.map(lambda value: value not in (None, "") and not (isinstance(value, float) and np.isnan(value)))
        shown = values[filled]
        hashable = shown.map(lambda value: str(value) if isinstance(value, (list, dict)) else value)
        rows.append({
            "Kolom": column,
            "Jenis": "angka" if pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values)
            else "teks",
            "Terisi (%)": round(float(filled.mean()) * 100, 1) if len(values) else 0.0,
            "Nilai berbeda": int(hashable.nunique()),
            "Contoh": "" if shown.empty else str(shown.iloc[0])[:80],
        })
    return pd.DataFrame(rows)


def numeric_summary(frame):
    numbers = frame.select_dtypes(include="number").drop(columns=["pekerjaan_asal"], errors="ignore")
    if numbers.empty:
        return pd.DataFrame()
    summary = numbers.describe().T[["count", "mean", "std", "min", "50%", "max"]]
    summary.columns = ["Jumlah", "Rata-rata", "Simpangan baku", "Minimum", "Median", "Maksimum"]
    return summary.round(2).rename_axis("Kolom").reset_index()


def category_counts(series, top=15):
    counts = series.dropna().map(lambda value: str(value)).value_counts().head(top)
    return counts.rename_axis("Nilai").reset_index(name="Jumlah")


# --------------------------------------------------------------------------- #
# Teks
# --------------------------------------------------------------------------- #

def words_config():
    return {**pp.FULL, "stemming": False, "drop_duplicates": False, "keep_negation": True,
            "stopword_sources": ["project"], "extra_slang": "", "extra_stopwords": "", "keep_words": ""}


def cleaned_documents(rows, columns, limit=MAX_DOCUMENTS):
    """Cleaned, non-empty documents (at most `limit`) and how many rows were skipped."""
    if columns == [CLEAN_COLUMN]:
        documents = [pp.join_columns(row, columns) for row in rows]
    else:
        config = words_config()
        resources = pp.build_resources(config)
        stats = pp.new_stats()
        documents = [pp.clean_text(pp.join_columns(row, columns), config, resources, stats) for row in rows]
    kept = [text for text in documents if text]
    return kept[:limit], len(rows) - len(kept)


def word_counts(documents, top=30):
    counts = Counter(word for text in documents for word in text.split())
    return pd.DataFrame(counts.most_common(top), columns=["Kata", "Jumlah"])


def bigram_counts(documents, top=20):
    counts = Counter()
    for text in documents:
        words = text.split()
        counts.update(" ".join(pair) for pair in zip(words, words[1:]))
    return pd.DataFrame(counts.most_common(top), columns=["Pasangan kata", "Jumlah"])


def topics(documents, count=5, terms=8):
    """Topic terms and sizes from TF-IDF + NMF. Returns (topic table, document→topic list)."""
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import TfidfVectorizer

    if len(documents) < max(count * 3, 10):
        raise ValueError("Dokumen terlalu sedikit untuk jumlah topik ini. Kurangi jumlah topik atau tambah data.")
    vectorizer = TfidfVectorizer(max_df=0.7, min_df=2, max_features=3000, token_pattern=r"(?u)\b\w\w+\b")
    matrix = vectorizer.fit_transform(documents)
    if matrix.shape[1] < count:
        raise ValueError("Kosakata terlalu sedikit untuk jumlah topik ini. Kurangi jumlah topik.")
    model = NMF(n_components=count, init="nndsvda", random_state=0, max_iter=300)
    weights = model.fit_transform(matrix)
    vocabulary = np.array(vectorizer.get_feature_names_out())
    assignment = weights.argmax(axis=1)
    assignment[~(weights > 0).any(axis=1)] = count
    rows = []
    for number, component in enumerate(model.components_):
        ranked = component.argsort()[::-1]
        top_terms = vocabulary[ranked[component[ranked] > 0][:terms]]
        rows.append({"Topik": f"Topik {number + 1}", "Dokumen": int((assignment == number).sum()),
                     "Kata utama": ", ".join(top_terms)})
    unassigned = int((assignment == count).sum())
    if unassigned:
        rows.append({"Topik": "Tanpa topik", "Dokumen": unassigned, "Kata utama": ""})
    return pd.DataFrame(rows), assignment.tolist()


# --------------------------------------------------------------------------- #
# Tren waktu
# --------------------------------------------------------------------------- #

def volume_over_time(frame, column, period="D", group=None, top_groups=5):
    """Rows per period. With `group`, the `top_groups` largest groups side by side."""
    dates = parse_dates(frame[column])
    data = pd.DataFrame({"tanggal": dates}).dropna()
    if data.empty:
        return pd.DataFrame(), 0
    skipped = len(frame) - len(data)
    if group:
        data["kelompok"] = frame.loc[data.index, group].astype(str)
        keep = data["kelompok"].value_counts().head(top_groups).index
        data = data[data["kelompok"].isin(keep)]
        table = (data.groupby([pd.Grouper(key="tanggal", freq=period), "kelompok"]).size()
                 .unstack(fill_value=0))
    else:
        table = data.groupby(pd.Grouper(key="tanggal", freq=period)).size().to_frame("Jumlah")
    return table, skipped


# --------------------------------------------------------------------------- #
# Jaringan kata
# --------------------------------------------------------------------------- #

def pair_weights(documents, words):
    """Co-occurrence counts of the `words` most frequent words, within one document."""
    frequency = Counter(word for text in documents for word in set(text.split()))
    allowed = {word for word, _ in frequency.most_common(words)}
    pairs = Counter()
    for text in documents:
        pairs.update(combinations(sorted(allowed.intersection(text.split())), 2))
    return frequency, pairs


def suggested_min_edge(documents, words, per_word=2.5):
    """A threshold that keeps about `per_word` lines per word, so the graph stays readable."""
    _, pairs = pair_weights(documents, words)
    ranked = sorted(pairs.values(), reverse=True)
    keep = int(words * per_word)
    return max(2, ranked[keep - 1] if len(ranked) >= keep else 2)


def word_network(documents, words=30, min_edge=2):
    """Co-occurrence of the most frequent words within one document.

    Returns (nodes, edges): nodes have word, count, x, y; edges have a, b, weight.
    """
    frequency, pairs = pair_weights(documents, words)
    top = [word for word, _ in frequency.most_common(words)]
    edges = [(a, b, weight) for (a, b), weight in pairs.items() if weight >= min_edge]
    connected = {word for a, b, _ in edges for word in (a, b)}
    top = [word for word in top if word in connected]
    if not top:
        return pd.DataFrame(), pd.DataFrame()
    positions = spring_layout(top, edges)
    nodes = pd.DataFrame({"word": top, "count": [frequency[word] for word in top],
                          "x": [positions[word][0] for word in top], "y": [positions[word][1] for word in top]})
    return nodes, pd.DataFrame(edges, columns=["a", "b", "weight"])


def spring_layout(nodes, edges, iterations=200, seed=7):
    """Fruchterman-Reingold layout in numpy; small graphs only (tens of nodes)."""
    rng = np.random.default_rng(seed)
    index = {word: number for number, word in enumerate(nodes)}
    position = rng.uniform(-1, 1, (len(nodes), 2))
    pairs = np.array([(index[a], index[b]) for a, b, _ in edges if a in index and b in index])
    weights = np.array([weight for a, b, weight in edges if a in index and b in index], dtype=float)
    if len(pairs):
        weights = weights / weights.max()
    optimal = 1 / np.sqrt(len(nodes))
    temperature = 0.2
    for _ in range(iterations):
        delta = position[:, None, :] - position[None, :, :]
        distance = np.maximum(np.linalg.norm(delta, axis=-1), 0.01)
        displacement = (delta / distance[..., None] * (optimal ** 2 / distance)[..., None]).sum(axis=1)
        if len(pairs):
            link = position[pairs[:, 0]] - position[pairs[:, 1]]
            length = np.maximum(np.linalg.norm(link, axis=1), 0.01)
            pull = link * (length / optimal * weights)[:, None]
            np.add.at(displacement, pairs[:, 0], -pull)
            np.add.at(displacement, pairs[:, 1], pull)
        size = np.maximum(np.linalg.norm(displacement, axis=1), 0.01)
        position += displacement / size[:, None] * np.minimum(size, temperature)[:, None]
        temperature *= 0.98
    position -= position.mean(axis=0)
    position /= max(np.abs(position).max(), 1e-9)
    return {word: tuple(position[number]) for word, number in index.items()}


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "analisis"
