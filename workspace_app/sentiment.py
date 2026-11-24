"""Sentiment for reviews and other text: plain functions, no Streamlit.

Three parts, following docs/ANALISIS.md (family A):
- a transformer model reads the original text after light normalisation (`Classifier`);
- for reviews, a TF-IDF + logistic regression classifier learns from the stars of the dataset itself;
- an editable aspect dictionary picks the sentences about each aspect, and the model rates those sentences.

`run` does all three for a list of rows and is what the background job calls. The summaries at the end
read the derived dataset it produces.
"""
from collections import Counter, defaultdict
from pathlib import Path
import re
from time import monotonic

import numpy as np
import pandas as pd

from workspace_app import analysis as an
from workspace_app import preprocess as pp

MODEL_ID = "w11wo/indonesian-roberta-base-sentiment-classifier"
MODEL_LICENSE = "MIT"
MODEL_SIZE_MB = 500
MODEL_FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
               "vocab.json", "merges.txt", "model.safetensors"]
MODEL_LABELS = {"positive": "positif", "neutral": "netral", "negative": "negatif"}
SENTIMENTS = ("positif", "netral", "negatif")
MAX_TOKENS = 256
BATCH = 32
SEED = 42
MIN_STAR_ROWS = 200
MIN_PER_CLASS = 30
ASPECT_PREFIX = "aspek_"

ASPECT_PRESETS = {
    "Aplikasi": {
        "login": ["login", "masuk", "akun", "otp", "verifikasi", "kode", "password", "sandi"],
        "iklan": ["iklan"],
        "error": ["error", "bug", "crash", "force close", "keluar sendiri", "macet", "lemot", "lambat",
                  "loading", "hang", "gagal"],
        "update": ["update", "pembaruan", "versi"],
        "harga": ["harga", "mahal", "murah", "bayar", "biaya", "langganan", "premium"],
        "fitur": ["fitur", "tampilan", "desain", "menu"],
        "layanan": ["cs", "customer service", "layanan", "pelayanan", "respon"],
    },
    "Tempat": {
        "pelayanan": ["pelayanan", "pelayan", "staf", "karyawan", "petugas", "ramah", "jutek", "layanan"],
        "harga": ["harga", "mahal", "murah", "bayar", "tiket"],
        "kebersihan": ["bersih", "kotor", "kebersihan", "toilet", "wc", "sampah", "bau"],
        "lokasi": ["lokasi", "akses", "jalan", "macet", "jauh", "dekat", "strategis"],
        "parkir": ["parkir", "parkiran"],
        "fasilitas": ["fasilitas", "mushola", "musala", "kursi", "ac", "wifi"],
        "makanan": ["rasa", "enak", "makanan", "minuman", "porsi"],
    },
}
SOURCE_PRESETS = {"playstore": "Aplikasi", "appstore": "Aplikasi", "gmaps": "Tempat"}
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #

def light_config():
    return {**pp.LIGHT, **pp.DEFAULT_LISTS, "drop_duplicates": False, "extra_slang": "", "extra_stopwords": "",
            "keep_words": ""}


class Normaliser:
    """Light normalisation for the model: slang, emoji as words, URLs as tokens; words are kept."""

    def __init__(self):
        self.config = light_config()
        self.resources = pp.build_resources(self.config)
        self.stats = pp.new_stats()

    def __call__(self, text):
        return pp.clean_text(text, self.config, self.resources, self.stats)


def sentences(text):
    return [part.strip() for part in SENTENCE_RE.split(str(text or "")) if part.strip()]


def parse_aspects(text):
    """'aspek: kata, kata' per line → {aspek: [kata, ...]}; blank and malformed lines are skipped."""
    aspects = {}
    for line in str(text or "").splitlines():
        name, _, words = line.partition(":")
        name = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
        words = [word.strip().lower() for word in words.split(",") if word.strip()]
        if name and words:
            aspects[name] = list(dict.fromkeys(aspects.get(name, []) + words))
    return aspects


def format_aspects(aspects):
    return "\n".join(f"{name}: {', '.join(words)}" for name, words in aspects.items())


def aspect_patterns(aspects):
    return {name: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(word) for word in words) + r")(?!\w)")
            for name, words in aspects.items()}


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

def model_cache(directory):
    return Path(directory) / "models"


def model_path(directory, download=False):
    """Local folder of the model; downloads it when `download` is set. None when it is not on disk yet."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(MODEL_ID, cache_dir=str(model_cache(directory)), allow_patterns=MODEL_FILES,
                                 local_files_only=not download)
    except Exception:
        if download:
            raise
        return None


def device_name():
    import torch

    return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


class Classifier:
    """The sentiment model. `predict(texts)` returns one {positif, netral, negatif} probability dict per text."""

    def __init__(self, path, device=None):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.manual_seed(SEED)
        self.torch = torch
        self.device = device or device_name()
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).to(self.device).eval()
        self.labels = [MODEL_LABELS[self.model.config.id2label[index].lower()]
                       for index in range(self.model.config.num_labels)]
        self.revision = Path(path).name

    def predict(self, texts):
        if not texts:
            return []
        with self.torch.no_grad():
            batch = self.tokenizer(list(texts), padding=True, truncation=True, max_length=MAX_TOKENS,
                                   return_tensors="pt").to(self.device)
            probabilities = self.torch.softmax(self.model(**batch).logits, dim=-1).float().cpu().numpy()
        return [dict(zip(self.labels, map(float, row))) for row in probabilities]


def label_of(probabilities):
    label = max(probabilities, key=probabilities.get)
    return label, probabilities[label]


def predict_all(classifier, texts, *, batch=BATCH, stop=None, progress=None):
    """Probabilities for every text, in batches. Stops early (returning what is done) when `stop` is set."""
    result = []
    for start in range(0, len(texts), batch):
        if stop is not None and stop.is_set():
            break
        result.extend(classifier.predict(texts[start:start + batch]))
        if progress:
            progress(len(result), len(texts))
    return result


# --------------------------------------------------------------------------- #
# Stars
# --------------------------------------------------------------------------- #

def star_polarity(value):
    try:
        stars = float(value)
    except (TypeError, ValueError):
        return None
    if 1 <= stars <= 2:
        return "negatif"
    if 4 <= stars <= 5:
        return "positif"
    if stars == 3:
        return "netral"
    return None


def star_classifier(documents, ratings, seed=SEED):
    """Learn positive/negative from the stars (1–2 vs 4–5) of this dataset.

    Returns (predictions, report). Predictions has one (label, probability) per document, None for an empty
    one. When there are too few labelled reviews, predictions is None and report holds the reason.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline

    labels = [star_polarity(value) for value in ratings]
    usable = [index for index, (text, label) in enumerate(zip(documents, labels))
              if text and label in ("positif", "negatif")]
    counts = Counter(labels[index] for index in usable)
    if len(usable) < MIN_STAR_ROWS or min(counts.get("positif", 0), counts.get("negatif", 0)) < MIN_PER_CLASS:
        return None, {"alasan": (f"Butuh minimal {MIN_STAR_ROWS} ulasan berteks dengan bintang 1–2 atau 4–5 dan "
                                 f"minimal {MIN_PER_CLASS} per kelas; tersedia {len(usable)} "
                                 f"({counts.get('positif', 0)} positif, {counts.get('negatif', 0)} negatif).")}

    def model():
        return make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=50_000, sublinear_tf=True),
            LogisticRegression(max_iter=1000, class_weight="balanced", random_state=seed),
        )

    texts = [documents[index] for index in usable]
    targets = [labels[index] for index in usable]
    train_x, test_x, train_y, test_y = train_test_split(texts, targets, test_size=0.2, stratify=targets,
                                                        random_state=seed)
    evaluated = model().fit(train_x, train_y)
    guessed = evaluated.predict(test_x)
    classes = ["positif", "negatif"]
    report = {
        "data_latih": len(train_x), "data_uji": len(test_x), "seed": seed,
        "kelas": dict(counts),
        "akurasi": float(accuracy_score(test_y, guessed)),
        "f1_makro": float(f1_score(test_y, guessed, average="macro")),
        "per_kelas": {name: {key: float(value) for key, value in values.items()}
                      for name, values in classification_report(test_y, guessed, labels=classes,
                                                                output_dict=True, zero_division=0).items()
                      if name in classes},
        "matriks_kebingungan": {"label": classes,
                                "nilai": confusion_matrix(test_y, guessed, labels=classes).tolist()},
    }
    final = model().fit(texts, targets)
    filled = [index for index, text in enumerate(documents) if text]
    predictions = [None] * len(documents)
    if filled:
        probabilities = final.predict_proba([documents[index] for index in filled])
        order = list(final.classes_)
        for index, row in zip(filled, probabilities):
            best = int(np.argmax(row))
            predictions[index] = (order[best], float(row[best]))
    return predictions, report


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def run(rows, columns, options, classifier, *, stop=None, log=print, checkpoint=None):
    """Sentiment for `rows`; returns (output rows, metadata).

    options: rating_column (str or None), aspects ({name: [words]}), star (bool).
    `classifier` is a `Classifier` (or anything with `predict` and `revision`). `checkpoint(rows)` receives
    the rows done so far, so a stopped job keeps them.
    """
    started = monotonic()
    normalise = Normaliser()
    raw = [pp.join_columns(row, columns) for row in rows]
    texts = [normalise(text) if text else "" for text in raw]
    filled = [index for index, text in enumerate(texts) if text]
    log(f"{len(filled):,} dari {len(rows):,} baris berisi teks.".replace(",", "."))

    def output(done_count):
        return [{**row, **extra[index]} for index, row in enumerate(rows[:done_count])]

    extra = [{"sentimen": "", "skor_sentimen": None} for _ in rows]
    done, last = 0, monotonic()

    def progress(count, total):
        nonlocal done, last
        for position in range(done, count):
            index = filled[position]
            label, score = label_of(probabilities[position])
            extra[index].update(sentimen=label, skor_sentimen=round(score, 4))
        done = count
        if monotonic() - last >= 15 or count == total:
            log(f"Sentimen: {count:,} dari {total:,} teks.".replace(",", "."))
            last = monotonic()
            if checkpoint:
                checkpoint(output(filled[count - 1] + 1 if count else 0))

    probabilities = []
    for start in range(0, len(filled), BATCH):
        if stop is not None and stop.is_set():
            break
        probabilities.extend(classifier.predict([texts[index] for index in filled[start:start + BATCH]]))
        progress(len(probabilities), len(filled))
    stopped = stop is not None and stop.is_set()
    covered = filled[len(probabilities) - 1] + 1 if probabilities else (0 if filled else len(rows))
    if stopped:
        log("Dihentikan; baris yang sudah dinilai disimpan.")
        return output(covered), {"dihentikan": True, **_metadata(classifier, columns, options, rows, started)}

    aspects = options.get("aspects") or {}
    aspect_report = {}
    if aspects:
        aspect_report = _aspects(raw, normalise, aspects, classifier, extra, stop, log)

    star_report = None
    rating = options.get("rating_column")
    if rating and options.get("star", True):
        log("Melatih classifier dari bintang.")
        config = an.words_config()
        resources = pp.build_resources(config)
        stats = pp.new_stats()
        documents = [pp.clean_text(text, config, resources, stats) if text else "" for text in raw]
        predictions, star_report = star_classifier(documents, [row.get(rating) for row in rows])
        if predictions is None:
            log(star_report["alasan"])
        else:
            for index, prediction in enumerate(predictions):
                extra[index]["sentimen_bintang"] = prediction[0] if prediction else ""
                extra[index]["skor_sentimen_bintang"] = round(prediction[1], 4) if prediction else None
            log(f"Classifier bintang: akurasi {star_report['akurasi']:.3f}, F1 makro {star_report['f1_makro']:.3f}.")
    if rating:
        for index, row in enumerate(rows):
            extra[index]["polaritas_bintang"] = star_polarity(row.get(rating)) or ""

    metadata = _metadata(classifier, columns, options, rows, started)
    metadata.update(teks=len(filled), sentimen=dict(Counter(item["sentimen"] for item in extra if item["sentimen"])),
                    classifier_bintang=star_report, aspek=aspect_report)
    log("Selesai.")
    return output(len(rows)), metadata


def _metadata(classifier, columns, options, rows, started):
    return {
        "model": MODEL_ID, "lisensi_model": MODEL_LICENSE, "revisi_model": getattr(classifier, "revision", ""),
        "perangkat": getattr(classifier, "device", ""), "token_maksimal": MAX_TOKENS, "batch": BATCH,
        "seed": SEED, "kolom": list(columns), "kolom_rating": options.get("rating_column"),
        "kamus_aspek": options.get("aspects") or {}, "baris": len(rows),
        "detik": round(monotonic() - started, 1),
    }


def _aspects(raw, normalise, aspects, classifier, extra, stop, log):
    """Rate the sentences that mention each aspect; one label per row and aspect (mean of its sentences)."""
    patterns = aspect_patterns(aspects)
    mentions = defaultdict(list)  # (row, aspect) → sentence texts
    for index, text in enumerate(raw):
        for sentence in sentences(text):
            clean = normalise(sentence)
            for name, pattern in patterns.items():
                if clean and pattern.search(clean):
                    mentions[(index, name)].append(clean)
    unique = list(dict.fromkeys(sentence for found in mentions.values() for sentence in found))
    log(f"Aspek: {len(unique):,} kalimat menyebut minimal satu aspek.".replace(",", "."))
    scored = dict(zip(unique, predict_all(classifier, unique, stop=stop)))
    for item in extra:
        for name in aspects:
            item[ASPECT_PREFIX + name] = ""
    for (index, name), found in mentions.items():
        rated = [scored[sentence] for sentence in found if sentence in scored]
        if rated:
            mean = {label: float(np.mean([probabilities[label] for probabilities in rated])) for label in SENTIMENTS}
            extra[index][ASPECT_PREFIX + name] = label_of(mean)[0]
    return {name: sum(1 for (_, aspect) in mentions if aspect == name) for name in aspects}


# --------------------------------------------------------------------------- #
# Summaries of a derived dataset
# --------------------------------------------------------------------------- #

def _rated(frame, column="sentimen"):
    return frame[frame[column].isin(SENTIMENTS)] if column in frame else frame.iloc[0:0]


def distribution(frame, column="sentimen"):
    rated = _rated(frame, column)
    counts = rated[column].value_counts().reindex(SENTIMENTS, fill_value=0)
    total = int(counts.sum())
    return pd.DataFrame({"Sentimen": counts.index, "Jumlah": counts.values,
                         "Persen": (counts.values / total * 100).round(1) if total else 0.0})


def by_rating(frame, rating):
    rated = _rated(frame).copy()
    rated["Bintang"] = pd.to_numeric(rated[rating], errors="coerce")
    rated = rated.dropna(subset=["Bintang"])
    if rated.empty:
        return pd.DataFrame()
    table = pd.crosstab(rated["Bintang"].astype(int), rated["sentimen"], normalize="index").reindex(
        columns=list(SENTIMENTS), fill_value=0) * 100
    table.insert(0, "Ulasan", rated.groupby(rated["Bintang"].astype(int)).size())
    return table.round(1).reset_index()


def mismatches(frame, rating, text_columns, top=200):
    """Reviews whose stars and text disagree: 4–5 stars but negative text, or 1–2 stars but positive text."""
    rated = _rated(frame).copy()
    polarity = rated[rating].map(star_polarity)
    wrong = rated[((polarity == "positif") & (rated["sentimen"] == "negatif"))
                  | ((polarity == "negatif") & (rated["sentimen"] == "positif"))].copy()
    if wrong.empty:
        return wrong
    wrong["Teks"] = [pp.join_columns(row, text_columns) for row in wrong.to_dict("records")]
    wrong = wrong.sort_values("skor_sentimen", ascending=False)
    columns = [rating, "sentimen", "skor_sentimen", "Teks"] + [name for name in ("tanggal", "versi_aplikasi")
                                                               if name in wrong]
    return wrong[columns].head(top)


def agreement(frame, rating, column="sentimen"):
    """How often the stars (1–2 / 4–5) and a sentiment column agree, with Cohen's kappa."""
    from sklearn.metrics import cohen_kappa_score

    rated = _rated(frame, column)
    polarity = rated[rating].map(star_polarity)
    keep = polarity.isin(["positif", "negatif"])
    stars, labels = polarity[keep], rated.loc[keep, column]
    if len(stars) < 2 or stars.nunique() < 2:
        return None
    return {"n": int(len(stars)), "sesuai": float((stars == labels).mean() * 100),
            "kappa": float(cohen_kappa_score(stars, labels, labels=list(SENTIMENTS)))}


def aspect_columns(frame):
    return [column for column in frame if str(column).startswith(ASPECT_PREFIX)]


def aspect_summary(frame):
    rows = []
    for column in aspect_columns(frame):
        mentioned = frame[column][frame[column].isin(SENTIMENTS)]
        counts = mentioned.value_counts()
        total = len(mentioned)
        rows.append({"Aspek": column[len(ASPECT_PREFIX):], "Disebut": total,
                     "Cakupan (%)": round(total / len(frame) * 100, 1) if len(frame) else 0.0,
                     **{f"{label.capitalize()} (%)": round(counts.get(label, 0) / total * 100, 1) if total else 0.0
                        for label in SENTIMENTS}})
    return pd.DataFrame(rows).sort_values("Disebut", ascending=False) if rows else pd.DataFrame()


def trend(frame, date_column, period="ME"):
    """Share of each sentiment per period (percent) and the number of rated texts."""
    rated = _rated(frame).copy()
    rated["Waktu"] = an.parse_dates(rated[date_column])
    rated = rated.dropna(subset=["Waktu"])
    if rated.empty:
        return pd.DataFrame()
    grouped = rated.groupby([pd.Grouper(key="Waktu", freq=period), "sentimen"]).size().unstack(fill_value=0)
    grouped = grouped.reindex(columns=list(SENTIMENTS), fill_value=0)
    totals = grouped.sum(axis=1)
    shares = grouped.div(totals.replace(0, np.nan), axis=0).mul(100).round(1)
    shares.insert(0, "Teks", totals)
    return shares[totals > 0].reset_index()


def by_version(frame, top=15, date_column=None):
    """Share of negative text per app version, for the most reviewed versions, oldest first."""
    rated = _rated(frame)
    rated = rated[rated["versi_aplikasi"].astype(str).str.strip() != ""]
    if rated.empty:
        return pd.DataFrame()
    counts = rated["versi_aplikasi"].value_counts().head(top)
    chosen = rated[rated["versi_aplikasi"].isin(counts.index)]
    table = chosen.groupby("versi_aplikasi")["sentimen"].value_counts(normalize=True).unstack(fill_value=0)
    table = (table.reindex(columns=list(SENTIMENTS), fill_value=0) * 100).round(1)
    table.insert(0, "Ulasan", counts)
    if date_column:
        first = an.parse_dates(chosen[date_column]).groupby(chosen["versi_aplikasi"]).min()
        table = table.loc[first.sort_values().index]
    return table.reset_index().rename(columns={"versi_aplikasi": "Versi"})


def method_text(metadata, origin):
    """A paragraph for a methods section."""
    parts = [
        f"Sentimen {origin} diklasifikasikan dengan model {metadata['model']} (RoBERTa bahasa Indonesia yang "
        f"dilatih pada SmSA IndoNLU; lisensi {metadata['lisensi_model']}, revisi "
        f"{str(metadata.get('revisi_model', ''))[:12]}) melalui pustaka transformers, pada teks kolom "
        f"{', '.join(metadata['kolom'])} yang dinormalisasi ringan (kata gaul, emoji menjadi kata, URL menjadi token) "
        f"dan dipotong pada {metadata['token_maksimal']} token. Label adalah kelas berprobabilitas tertinggi "
        f"(positif, netral, negatif)."
    ]
    if star := metadata.get("classifier_bintang"):
        if "akurasi" in star:
            parts.append(
                f"Sebagai pembanding, classifier TF-IDF (unigram dan bigram) dengan regresi logistik berbobot kelas "
                f"dilatih dari bintang ulasan (1–2 negatif, 4–5 positif; bintang 3 tidak dipakai untuk melatih) "
                f"dengan pembagian latih-uji 80:20 bertingkat (seed {star['seed']}); pada data uji "
                f"({star['data_uji']} ulasan) akurasinya {star['akurasi']:.3f} dan F1 makro {star['f1_makro']:.3f}.")
    if aspects := metadata.get("kamus_aspek"):
        parts.append(
            f"Sentimen per aspek ditentukan dengan kamus kata kunci ({', '.join(aspects)}): setiap kalimat yang "
            f"menyebut kata kunci suatu aspek dinilai model yang sama, lalu probabilitasnya dirata-ratakan per "
            f"ulasan dan aspek.")
    return " ".join(parts)
