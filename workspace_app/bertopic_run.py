"""BERTopic for news and other long texts (the model tier of family C): plain functions, no Streamlit.

Pipeline (Grootendorst, 2022): sentence embeddings of the original text (paraphrase-multilingual-MiniLM-L12-v2,
Apache-2.0) → UMAP → HDBSCAN → class-based TF-IDF over the cleaned text. The embeddings are computed here, in
batches that a stop request can interrupt, and handed to BERTopic precomputed. `run` is what the background
job calls. The derived dataset keeps the rows and adds `topik_id`, `topik_nama` and `topik_prob`; the topic
table, coherence and parameters go into the job metadata.
"""
from inspect import signature
from pathlib import Path
from time import monotonic

import numpy as np
import pandas as pd

from workspace_app import analysis as an
from workspace_app import news
from workspace_app import preprocess as pp
from workspace_app import sentiment as sn

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_LICENSE = "Apache-2.0"
MODEL_SIZE_MB = 480
MODEL_FILES = ["config.json", "config_sentence_transformers.json", "modules.json", "model.safetensors",
               "sentence_bert_config.json", "sentencepiece.bpe.model", "special_tokens_map.json",
               "tokenizer.json", "tokenizer_config.json", "1_Pooling/config.json"]
MAX_TOKENS = 256
BATCH = 32
SEED = 42
MIN_DOCUMENTS = 50
DEFAULTS = {"min_topic_size": 15, "n_neighbors": 15, "n_components": 5, "top_words": 10}
OUTLIER = -1
NO_TOPIC = "Tanpa topik"


# --------------------------------------------------------------------------- #
# Embedding model
# --------------------------------------------------------------------------- #

def model_path(directory, download=False):
    """Local folder of the embedding model; downloads it when `download` is set. None when not on disk yet."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(MODEL_ID, cache_dir=str(sn.model_cache(directory)), allow_patterns=MODEL_FILES,
                                 local_files_only=not download)
    except Exception:
        if download:
            raise
        return None


class Embedder:
    """`encode(texts)` returns one unit-length vector per text (numpy array)."""

    def __init__(self, path, device=None):
        import torch
        from sentence_transformers import SentenceTransformer

        torch.manual_seed(SEED)
        self.device = device or sn.device_name()
        self.model = SentenceTransformer(path, device=self.device)
        self.model.max_seq_length = MAX_TOKENS
        self.revision = Path(path).name

    def encode(self, texts):
        return self.model.encode(list(texts), batch_size=BATCH, show_progress_bar=False,
                                 convert_to_numpy=True, normalize_embeddings=True)


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def topic_label(number, words, size=4):
    return f"{number}: {', '.join(words[:size])}"


def run(rows, columns, options, embedder, *, stop=None, log=print):
    """Topics for `rows`; returns (output rows, metadata). A stopped run returns ([], {"dihentikan": True}):
    topics from a part of the embeddings would not mean anything.

    options: min_topic_size, n_neighbors, n_components, top_words (see DEFAULTS)."""
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    started = monotonic()
    options = {**DEFAULTS, **(options or {})}
    frame = pd.DataFrame(rows)
    raw = pd.Series([pp.join_columns(row, columns) for row in rows], index=frame.index).map(
        lambda text: " ".join(str(text).split()))
    cleaned = news.cleaned_rows(frame, columns, limit=len(frame))
    filled = [index for index in frame.index if raw[index] and cleaned[index]]
    log(f"{len(filled):,} dari {len(rows):,} baris berisi teks.".replace(",", "."))
    if len(filled) < MIN_DOCUMENTS:
        raise ValueError(f"BERTopic butuh minimal {MIN_DOCUMENTS} teks, dataset ini hanya {len(filled)}.")

    vectors = []
    for start in range(0, len(filled), BATCH * 8):
        if stop is not None and stop.is_set():
            log("Dihentikan sebelum topik dihitung.")
            return [], {"dihentikan": True}
        chunk = filled[start:start + BATCH * 8]
        vectors.append(embedder.encode([raw[index] for index in chunk]))
        log(f"Embedding: {min(start + len(chunk), len(filled)):,} dari {len(filled):,} teks.".replace(",", "."))
    embeddings = np.vstack(vectors)
    documents = [cleaned[index] for index in filled]

    log("Mengelompokkan topik (UMAP, HDBSCAN)…")
    # UMAP 0.6 will change its default layout; keeping the current one makes topics repeatable across versions.
    layout = {"compatibility_layout": True} if "compatibility_layout" in signature(UMAP).parameters else {}
    model = BERTopic(
        embedding_model=None,
        umap_model=UMAP(n_neighbors=options["n_neighbors"], n_components=options["n_components"], min_dist=0.0,
                        metric="cosine", random_state=SEED, **layout),
        hdbscan_model=HDBSCAN(min_cluster_size=options["min_topic_size"], metric="euclidean",
                              cluster_selection_method="eom"),
        # BERTopic fits this on one joined document per topic, so min_df above 1 would drop every word that
        # is typical of a single topic.
        vectorizer_model=CountVectorizer(token_pattern=r"(?u)\b\w\w+\b", min_df=1),
        top_n_words=options["top_words"], calculate_probabilities=False, verbose=False)
    topics, probabilities = model.fit_transform(documents, embeddings=embeddings)
    topics = [int(topic) for topic in topics]

    ids = sorted({topic for topic in topics if topic != OUTLIER})
    words = {topic: [word for word, _ in model.get_topic(topic)] for topic in ids}
    names = {topic: topic_label(topic, words[topic]) for topic in ids}
    scores, mean = news.npmi_coherence([words[topic] for topic in ids], documents, options["top_words"])
    sizes = {topic: topics.count(topic) for topic in ids}
    table = [{"id": topic, "nama": names[topic], "dokumen": sizes[topic], "kata": words[topic],
              "koherensi": None if score is None else round(score, 4)} for topic, score in zip(ids, scores)]
    table.sort(key=lambda item: -item["dokumen"])
    outliers = topics.count(OUTLIER)
    log(f"{len(ids)} topik; {outliers:,} teks ({outliers / len(topics) * 100:.1f}%) tanpa topik.".replace(",", "."))

    extra = [{"topik_id": "", "topik_nama": "", "topik_prob": None} for _ in rows]
    position = {index: number for number, index in enumerate(frame.index)}
    for index, topic, probability in zip(filled, topics, probabilities):
        extra[position[index]] = {
            "topik_id": topic, "topik_nama": names.get(topic, NO_TOPIC),
            "topik_prob": None if topic == OUTLIER else round(float(probability), 4)}
    output = [{**row, **extra[number]} for number, row in enumerate(rows)]
    return output, {
        "model": MODEL_ID, "lisensi_model": MODEL_LICENSE, "revisi_model": getattr(embedder, "revision", ""),
        "perangkat": getattr(embedder, "device", ""), "token_maksimal": MAX_TOKENS, "seed": SEED,
        "kolom": list(columns), "baris": len(rows), "teks": len(filled), "topik": table,
        "tanpa_topik": outliers, "koherensi_rata_rata": None if mean is None else round(mean, 4),
        "parameter": options, "versi": versions(), "detik": round(monotonic() - started, 1),
    }


def versions():
    from importlib.metadata import version

    return {name: version(name) for name in ("bertopic", "umap-learn", "hdbscan", "sentence-transformers")}


# --------------------------------------------------------------------------- #
# Summaries of a derived dataset
# --------------------------------------------------------------------------- #

def topic_table(metadata):
    """The topic table of a finished job as a DataFrame (largest first)."""
    rows = [{"Topik": item["nama"], "Dokumen": item["dokumen"], "Koherensi NPMI": item.get("koherensi"),
             "Kata utama": ", ".join(item["kata"])} for item in metadata.get("topik") or []]
    return pd.DataFrame(rows)


def over_time(frame, date_column, period="ME", top=8):
    """Share of the `top` largest topics per period (percent of the texts with a topic and a date)."""
    if "topik_nama" not in frame or date_column not in frame:
        return pd.DataFrame()
    data = pd.DataFrame({"periode": an.parse_dates(frame[date_column]), "topik": frame["topik_nama"]}).dropna()
    data = data[data["topik"].astype(str).ne("") & data["topik"].ne(NO_TOPIC)]
    if data.empty:
        return pd.DataFrame()
    keep = data["topik"].value_counts().index[:top]
    data = data[data["topik"].isin(keep)]
    shares = pd.crosstab(data["periode"].dt.to_period(period.rstrip("E")).dt.to_timestamp(), data["topik"])
    return shares.round(0).astype(int)


def documents_of(frame, topic_id, text_columns, count=5):
    """The texts of one topic with the highest membership probability."""
    if "topik_id" not in frame:
        return pd.DataFrame()
    ids = pd.to_numeric(frame["topik_id"], errors="coerce")
    found = frame[ids == topic_id].copy()
    found["_p"] = pd.to_numeric(found["topik_prob"], errors="coerce")
    found = found.sort_values("_p", ascending=False).head(count)
    out = pd.DataFrame({"Probabilitas": found["_p"].round(3).values,
                        "Teks": [pp.join_columns(row, text_columns)[:300] for row in found.to_dict("records")]})
    for name in ("portal", "title", "judul"):
        if name in found:
            out.insert(0, name.capitalize(), found[name].values)
            break
    return out


def method_text(metadata, origin):
    from workspace_app.social import decimal

    parameters = metadata.get("parameter") or DEFAULTS
    versions_ = metadata.get("versi") or {}
    coherence = metadata.get("koherensi_rata_rata")
    return (
        f"Topik {origin} (kolom {', '.join(metadata.get('kolom') or [])}) ditemukan dengan BERTopic "
        f"{versions_.get('bertopic', '')} (Grootendorst, 2022): teks asli diubah menjadi vektor kalimat dengan "
        f"{metadata.get('model', MODEL_ID)} (lisensi {metadata.get('lisensi_model', MODEL_LICENSE)}, revisi "
        f"{str(metadata.get('revisi_model', ''))[:12]}; dipotong pada {metadata.get('token_maksimal', MAX_TOKENS)} "
        f"token), direduksi ke {parameters['n_components']} dimensi dengan UMAP (n_neighbors "
        f"{parameters['n_neighbors']}, jarak kosinus, random_state {metadata.get('seed', SEED)}), dikelompokkan dengan "
        f"HDBSCAN (ukuran kelompok minimal {parameters['min_topic_size']}), dan setiap topik dirangkum dengan "
        f"{parameters['top_words']} kata berbobot c-TF-IDF dari teks yang dibersihkan tanpa stemming. Dari "
        f"{metadata.get('teks')} teks, {metadata.get('tanpa_topik')} tidak masuk topik mana pun (outlier HDBSCAN) "
        f"dan diperoleh {len(metadata.get('topik') or [])} topik."
        + (f" Koherensi topik rata-rata (NPMI, kemunculan bersama dalam dokumen) {decimal(coherence, 3)}."
           if coherence is not None else "")
        + " Topik adalah dugaan statistik: nama dan batasnya perlu diperiksa dengan membaca teks wakilnya."
    )
