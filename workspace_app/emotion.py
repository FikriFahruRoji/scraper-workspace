"""Emotion and toxicity for social media text (family B in docs/ANALISIS.md): plain functions, no Streamlit.

Three optional transformer models, chosen after a license check (2026-11-25):
- emotion: StevenLimcorn/indonesian-roberta-base-emotion-classifier (MIT; IndoNLU EmoT, Apache-2.0);
- hate speech and insult: Exqrch/IndoBERTweet-HateSpeech and -Insult (CC BY-SA 4.0; IndoToxic2024,
  Apache-2.0). Their repositories carry no tokenizer, so the one of indolem/indobertweet-base-uncased
  (Apache-2.0) is downloaded without its weights.

`run` rates a list of rows with the chosen models and is what the background job calls. Like the sentiment
model, the models read the original text after light normalisation.
"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

import pandas as pd

from workspace_app import preprocess as pp
from workspace_app import sentiment as sn

SEED = 42
MAX_TOKENS = 256
BATCH = 32
CONFIDENT = 0.6  # an emotion below this probability is counted as unclear in the summaries
TOXIC = 0.5  # probability of the positive class at which a text is flagged

EMOTIONS = ("senang", "cinta", "sedih", "marah", "takut")
EMOTION_LABELS = {"happy": "senang", "love": "cinta", "sadness": "sedih", "anger": "marah", "fear": "takut"}
TOKENIZER_FILES = ["config.json", "vocab.txt", "tokenizer_config.json", "special_tokens_map.json",
                   "added_tokens.json"]
TOXIC_FILES = ["config.json", "model.safetensors"]


@dataclass(frozen=True)
class Spec:
    key: str  # also the output column
    title: str
    model_id: str
    license: str
    size_mb: int
    files: list
    citation: str
    metric: str  # what the model card reports
    tokenizer_id: str | None = None
    labels: dict = field(default_factory=dict)  # model label → our label; empty for 0/1 detectors


TOKENIZER_ID = "indolem/indobertweet-base-uncased"
SPECS = {
    "emosi": Spec(
        "emosi", "Emosi", "StevenLimcorn/indonesian-roberta-base-emotion-classifier", "MIT", 500,
        sn.MODEL_FILES, "Limcorn (2023); data EmoT dari IndoNLU (Wilie dkk., 2020; Saputri dkk., 2018)",
        "F1 makro 0,72 pada data validasi EmoT (kartu model)", labels=EMOTION_LABELS),
    "ujaran_kebencian": Spec(
        "ujaran_kebencian", "Ujaran kebencian", "Exqrch/IndoBERTweet-HateSpeech", "CC BY-SA 4.0", 440,
        TOXIC_FILES, "Susanto dkk. (2024), IndoToxic2024",
        "akurasi 0,89 dan F1 makro 0,78, validasi silang 10 lipatan (kartu model)", tokenizer_id=TOKENIZER_ID),
    "hinaan": Spec(
        "hinaan", "Hinaan", "Exqrch/IndoBERTweet-Insult", "CC BY-SA 4.0", 440, TOXIC_FILES,
        "Susanto dkk. (2024), IndoToxic2024",
        "akurasi 0,79 dan F1 makro 0,85, validasi silang 10 lipatan (kartu model)", tokenizer_id=TOKENIZER_ID),
}
TOXIC_KEYS = ("ujaran_kebencian", "hinaan")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

def _snapshot(directory, repo, files, download):
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo, cache_dir=str(sn.model_cache(directory)), allow_patterns=files,
                                 local_files_only=not download)
    except Exception:
        if download:
            raise
        return None


def model_paths(directory, key, download=False):
    """(model folder, tokenizer folder) of a model; None when it is not on disk yet (and not downloaded)."""
    spec = SPECS[key]
    path = _snapshot(directory, spec.model_id, spec.files, download)
    if path is None:
        return None
    tokenizer = path
    if spec.tokenizer_id:
        tokenizer = _snapshot(directory, spec.tokenizer_id, TOKENIZER_FILES, download)
        if tokenizer is None:
            return None
    return path, tokenizer


def cached(directory, key):
    return model_paths(directory, key) is not None


def download_size(directory, keys):
    """MB still to download for the chosen models."""
    return sum(SPECS[key].size_mb for key in keys if not cached(directory, key))


class Classifier:
    """One model. `predict(texts)` returns a probability dict per text: emotions for the emotion model,
    {"tidak", "ya"} for a detector."""

    def __init__(self, key, paths, device=None):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.manual_seed(SEED)
        model_path, tokenizer_path = paths
        spec = SPECS[key]
        self.key, self.torch = key, torch
        self.device = device or sn.device_name()
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path).to(self.device).eval()
        names = [self.model.config.id2label[index] for index in range(self.model.config.num_labels)]
        if spec.labels:
            self.labels = [spec.labels[str(name).lower()] for name in names]
        else:  # the detectors are 0 = not, 1 = yes (model card examples)
            self.labels = ["tidak", "ya"]
        self.revision = Path(model_path).name

    def predict(self, texts):
        if not texts:
            return []
        with self.torch.no_grad():
            batch = self.tokenizer(list(texts), padding=True, truncation=True, max_length=MAX_TOKENS,
                                   return_tensors="pt").to(self.device)
            probabilities = self.torch.softmax(self.model(**batch).logits, dim=-1).float().cpu().numpy()
        return [dict(zip(self.labels, map(float, row))) for row in probabilities]


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def score_column(key):
    return f"skor_{key}"


def empty_values(keys):
    return {column: value for key in keys for column, value in ((key, ""), (score_column(key), None))}


def values_for(key, probabilities):
    """Output columns of one model for one text."""
    if key == "emosi":
        label, score = sn.label_of(probabilities)
        return {key: label, score_column(key): round(score, 4)}
    score = probabilities["ya"]
    return {key: "ya" if score >= TOXIC else "tidak", score_column(key): round(score, 4)}


def run(rows, columns, keys, classifiers, *, stop=None, log=print, checkpoint=None):
    """Rate `rows` with the models in `keys`; returns (output rows, metadata).

    `classifiers` maps each key to a `Classifier` (or anything with `predict`). Texts are handled in batches
    by every model in turn, so a stopped job keeps a complete prefix of rows."""
    started = monotonic()
    normalise = sn.Normaliser()
    texts = [normalise(pp.join_columns(row, columns)) for row in rows]
    texts = [text or "" for text in texts]
    filled = [index for index, text in enumerate(texts) if text]
    log(f"{len(filled):,} dari {len(rows):,} baris berisi teks.".replace(",", "."))
    extra = [empty_values(keys) for _ in rows]
    done, last = 0, monotonic()
    for start in range(0, len(filled), BATCH):
        if stop is not None and stop.is_set():
            break
        batch = filled[start:start + BATCH]
        for key in keys:
            for index, probabilities in zip(batch, classifiers[key].predict([texts[i] for i in batch])):
                extra[index].update(values_for(key, probabilities))
        done = start + len(batch)
        if monotonic() - last >= 15 or done == len(filled):
            log(f"Emosi dan toksisitas: {done:,} dari {len(filled):,} teks.".replace(",", "."))
            last = monotonic()
            if checkpoint:
                checkpoint(_output(rows, extra, filled[done - 1] + 1))
    stopped = stop is not None and stop.is_set()
    covered = filled[done - 1] + 1 if done else (0 if filled and stopped else len(rows))
    metadata = _metadata(classifiers, keys, columns, rows, started)
    metadata.update(teks=done, ringkasan=summary_counts(extra[:covered], keys))
    if stopped and done < len(filled):
        log("Dihentikan; baris yang sudah dinilai disimpan.")
        return _output(rows, extra, covered), {**metadata, "dihentikan": True}
    log("Selesai.")
    return _output(rows, extra, len(rows)), metadata


def _output(rows, extra, count):
    return [{**row, **extra[index]} for index, row in enumerate(rows[:count])]


def summary_counts(extra, keys):
    counts = {}
    for key in keys:
        if key == "emosi":
            counts[key] = dict(Counter(item[key] for item in extra if item[key]))
            counts["emosi_yakin"] = dict(Counter(item[key] for item in extra
                                                 if item[key] and item[score_column(key)] >= CONFIDENT))
        else:
            counts[key] = dict(Counter(item[key] for item in extra if item[key]))
    return counts


def _metadata(classifiers, keys, columns, rows, started):
    return {
        "model": {key: {"id": SPECS[key].model_id, "lisensi": SPECS[key].license,
                        "revisi": getattr(classifiers[key], "revision", ""),
                        "tokenizer": SPECS[key].tokenizer_id or SPECS[key].model_id} for key in keys},
        "analisis": list(keys), "perangkat": getattr(next(iter(classifiers.values()), None), "device", ""),
        "token_maksimal": MAX_TOKENS, "batch": BATCH, "seed": SEED, "ambang_yakin": CONFIDENT,
        "ambang_toksik": TOXIC, "kolom": list(columns), "baris": len(rows),
        "detik": round(monotonic() - started, 1),
    }


# --------------------------------------------------------------------------- #
# Summaries of a derived dataset
# --------------------------------------------------------------------------- #

def emotion_table(frame, threshold=CONFIDENT):
    """Count and share per emotion, with texts below `threshold` counted as 'tidak jelas'."""
    if "emosi" not in frame:
        return pd.DataFrame()
    rated = frame[frame["emosi"].isin(EMOTIONS)]
    if rated.empty:
        return pd.DataFrame()
    scores = pd.to_numeric(rated[score_column("emosi")], errors="coerce").fillna(0)
    labels = rated["emosi"].where(scores >= threshold, "tidak jelas")
    order = [*EMOTIONS, "tidak jelas"]
    counts = labels.value_counts().reindex(order, fill_value=0)
    return pd.DataFrame({"Emosi": order, "Jumlah": counts.values,
                         "Persen": (counts.values / counts.sum() * 100).round(1)})


def toxic_table(frame, threshold=TOXIC):
    """Per detector: texts rated, flagged, and their share."""
    rows = []
    for key in TOXIC_KEYS:
        column = score_column(key)
        if column not in frame:
            continue
        scores = pd.to_numeric(frame[column], errors="coerce").dropna()
        if scores.empty:
            continue
        flagged = int((scores >= threshold).sum())
        rows.append({"Deteksi": SPECS[key].title, "Teks dinilai": len(scores), "Terdeteksi": flagged,
                     "Persen": round(flagged / len(scores) * 100, 1)})
    return pd.DataFrame(rows)


def over_time(frame, column, date_column, period="ME", labels=None):
    """Share of each label of `column` per period (percent), from the rows with a readable date."""
    from workspace_app import analysis as an

    if column not in frame or date_column not in frame:
        return pd.DataFrame()
    dates = an.parse_dates(frame[date_column])
    data = pd.DataFrame({"periode": dates, "label": frame[column]}).dropna()
    data = data[data["label"].astype(str).ne("")]
    if labels:
        data = data[data["label"].isin(labels)]
    if data.empty:
        return pd.DataFrame()
    table = pd.crosstab(data["periode"].dt.to_period(period.rstrip("E")).dt.to_timestamp(), data["label"],
                        normalize="index") * 100
    return table.round(1)


def flagged_rows(frame, key, text_columns, top=200, threshold=TOXIC):
    """The texts a detector flags, most certain first."""
    column = score_column(key)
    if column not in frame:
        return pd.DataFrame()
    scores = pd.to_numeric(frame[column], errors="coerce")
    found = frame[scores >= threshold].assign(_skor=scores[scores >= threshold])
    if found.empty:
        return pd.DataFrame()
    table = pd.DataFrame({"Skor": found["_skor"].round(3),
                          "Teks": [pp.join_columns(row, text_columns) for row in found.to_dict("records")]})
    for name in ("username", "nama"):
        if name in found:
            table.insert(0, "Akun", found[name].values)
            break
    return table.sort_values("Skor", ascending=False).head(top).reset_index(drop=True)


def method_text(metadata, origin):
    keys = metadata.get("analisis") or []
    models = metadata.get("model") or {}
    parts = [
        f"Teks {origin} (kolom {', '.join(metadata.get('kolom') or [])}) dinormalisasi ringan (kata gaul, emoji "
        f"menjadi kata, URL menjadi token) dan dipotong pada {metadata.get('token_maksimal', MAX_TOKENS)} token "
        f"sebelum dinilai dengan pustaka transformers."
    ]
    if "emosi" in keys:
        spec = SPECS["emosi"]
        parts.append(
            f"Emosi diklasifikasikan dengan model {spec.model_id} (RoBERTa bahasa Indonesia; lisensi {spec.license}, "
            f"revisi {str(models.get('emosi', {}).get('revisi', ''))[:12]}) yang dilatih pada data EmoT dari IndoNLU "
            f"ke dalam lima kelas (senang, cinta, sedih, marah, takut); {spec.metric}. Model tidak memiliki kelas "
            f"netral, sehingga teks dengan probabilitas kelas tertinggi di bawah "
            f"{str(metadata.get('ambang_yakin', CONFIDENT)).replace('.', ',')} dilaporkan sebagai tidak jelas.")
    toxic = [key for key in keys if key in TOXIC_KEYS]
    if toxic:
        described = "; ".join(f"{SPECS[key].title.lower()} dengan {SPECS[key].model_id} (revisi "
                              f"{str(models.get(key, {}).get('revisi', ''))[:12]}; {SPECS[key].metric})"
                              for key in toxic)
        parts.append(
            f"Toksisitas dideteksi dengan model biner IndoBERTweet yang dilatih pada IndoToxic2024 (Susanto dkk., "
            f"2024; lisensi CC BY-SA 4.0) dan tokenizer {TOKENIZER_ID}: {described}. Teks ditandai bila "
            f"probabilitas kelas positif ≥ {str(metadata.get('ambang_toksik', TOXIC)).replace('.', ',')}. Label "
            f"toksisitas bersifat subjektif dan model dapat keliru pada sarkasme, bahasa daerah, dan campur kode, "
            f"sehingga hasilnya perlu dibaca ulang sebelum dijadikan temuan.")
    return " ".join(parts)
