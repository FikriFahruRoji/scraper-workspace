"""Named entities in news text (family C in docs/ANALISIS.md): plain functions, no Streamlit.

The model is cahya/NusaBert-ner-v1.3 (ModernBERT pretrained on Indonesian, MIT), fine-tuned on NERGrit
(19 labels, validation F1 0.82 on its model card). NERGrit itself carries no clear license; the user chose to
use the model anyway (2026-11-25), so the page and the methods text say so.

`run` tags a list of rows and is what the background job calls. The model reads the original text (cased,
not normalised): capital letters are the strongest cue for a name. Long texts are cut into overlapping
windows of `MAX_TOKENS` tokens.
"""
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
import re
from time import monotonic

import pandas as pd

from workspace_app import sentiment as sn

MODEL_ID = "cahya/NusaBert-ner-v1.3"
MODEL_LICENSE = "MIT"
DATA_NOTE = "data latih NERGrit tanpa lisensi yang jelas"
MODEL_SIZE_MB = 650
MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
               "special_tokens_map.json"]
METRIC = "F1 0,82 pada data validasi NERGrit (kartu model)"
SEED = 42
MAX_TOKENS = 1024
STRIDE = 128
BATCH = 8
MIN_SCORE = 0.5  # entities below this mean token probability are left out of the summaries

# NERGrit labels that name something. Numbers, dates, times, and amounts (CRD, DAT, MON, ORD, PRC, QTY, TIM)
# are dropped: they are not what an entity analysis of news looks for.
TYPES = {
    "PER": "Orang", "ORG": "Organisasi", "NOR": "Organisasi politik", "GPE": "Wilayah", "LOC": "Lokasi",
    "FAC": "Fasilitas", "EVT": "Peristiwa", "PRD": "Produk", "LAW": "Hukum", "WOA": "Karya",
    "REG": "Agama", "LAN": "Bahasa",
}
# Readable columns of the derived dataset: column → the types it lists.
GROUPS = {"entitas_orang": ("PER",), "entitas_organisasi": ("ORG", "NOR"),
          "entitas_tempat": ("GPE", "LOC", "FAC")}
COLUMN = "entitas"  # JSON list of {"teks", "jenis", "skor"}
LEADING = re.compile(r"^[\s\"'“”‘’(\[{,.;:–—-]+")
TRAILING = re.compile(r"[\s\"'“”‘’\]},.;:–—-]+$")  # a closing parenthesis is handled apart


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

def model_path(directory, download=False):
    """Local folder of the model; downloads it when `download` is set. None when it is not on disk yet."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(MODEL_ID, cache_dir=str(sn.model_cache(directory)), allow_patterns=MODEL_FILES,
                                 local_files_only=not download)
    except Exception:
        if download:
            raise
        return None


def clean_span(text):
    """An entity's text without surrounding spaces, quotes, and punctuation (but keeping e.g. 'PT X (Persero)')."""
    text = " ".join(str(text).split())
    while True:
        stripped = LEADING.sub("", TRAILING.sub("", text))
        if stripped.endswith(")") and stripped.count(")") > stripped.count("("):
            stripped = stripped[:-1]
        if stripped.count("(") > stripped.count(")"):
            stripped = stripped.rsplit("(", 1)[0]
        if stripped == text:
            return text
        text = stripped


def decode(labels, scores, offsets, text):
    """BIO tags of one window → [(start, end, type, mean score)], character offsets into `text`."""
    found, current = [], None
    for label, score, (start, end) in zip(labels, scores, offsets):
        if start == end:  # special or padding token
            continue
        prefix, _, kind = label.partition("-")
        if label == "O" or not kind:
            if current:
                found.append(current)
            current = None
            continue
        # A subword inside a word follows the word's entity whatever its own tag.
        # (BPE offsets put a word's leading space in its first token, so a token that starts with a letter
        # right where the previous one ended continues that word.)
        inside = (current is not None and start == current[1] and not text[start:start + 1].isspace()
                  and not text[start - 1:start].isspace())
        if current and (inside or (prefix == "I" and kind == current[2])):
            current = [current[0], end, current[2], current[3] + [score]]
        else:
            if current:
                found.append(current)
            current = [start, end, kind, [score]]
    if current:
        found.append(current)
    return [(start, end, kind, sum(values) / len(values)) for start, end, kind, values in found]


class Tagger:
    """The NER model. `tag(texts)` returns, per text, a list of {"teks", "jenis", "skor"}."""

    def __init__(self, path, device=None):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        torch.manual_seed(SEED)
        self.torch = torch
        self.device = device or sn.device_name()
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForTokenClassification.from_pretrained(path).to(self.device).eval()
        self.labels = [self.model.config.id2label[index] for index in range(self.model.config.num_labels)]
        self.revision = Path(path).name

    def tag(self, texts):
        if not texts:
            return []
        encoded = self.tokenizer(list(texts), truncation=True, max_length=MAX_TOKENS, stride=STRIDE,
                                 return_overflowing_tokens=True, return_offsets_mapping=True, padding=True,
                                 return_tensors="pt")
        owners = encoded.pop("overflow_to_sample_mapping").tolist()
        offsets = encoded.pop("offset_mapping").tolist()
        spans = [dict() for _ in texts]
        with self.torch.no_grad():
            for start in range(0, len(owners), BATCH):
                batch = {key: value[start:start + BATCH].to(self.device) for key, value in encoded.items()}
                probabilities = self.torch.softmax(self.model(**batch).logits, dim=-1).float().cpu()
                best, index = probabilities.max(dim=-1)
                for row in range(len(index)):
                    window = start + row
                    owner = owners[window]
                    labels = [self.labels[i] for i in index[row].tolist()]
                    for begin, end, kind, score in decode(labels, best[row].tolist(), offsets[window], texts[owner]):
                        # Overlapping windows find the same entity twice: keep the first.
                        if not any(begin < e and end > b for b, e in spans[owner]):
                            spans[owner][(begin, end)] = (kind, score)
        return [entities_of(text, found) for text, found in zip(texts, spans)]


def entities_of(text, spans):
    result = []
    for (begin, end), (kind, score) in sorted(spans.items()):
        if kind not in TYPES:
            continue
        name = clean_span(text[begin:end])
        # A name in edited text has a capital letter; all-lowercase spans ("pemerintah", English "the") are noise.
        if len(name) >= 2 and any(character.isupper() for character in name):
            result.append({"teks": name, "jenis": kind, "skor": round(float(score), 4)})
    return result


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def text_of(row, columns):
    """Title and body on separate lines, so an entity cannot run from one into the other."""
    return "\n".join(str(row[column]).strip() for column in columns
                     if isinstance(row.get(column), str) and row[column].strip())


def values_for(entities):
    values = {COLUMN: json.dumps(entities, ensure_ascii=False)}
    for column, kinds in GROUPS.items():
        names = [item["teks"] for item in entities if item["jenis"] in kinds and item["skor"] >= MIN_SCORE]
        values[column] = "; ".join(dict.fromkeys(names))
    return values


def empty_values():
    return {COLUMN: "", **{column: "" for column in GROUPS}}


def run(rows, columns, tagger, *, stop=None, log=print, checkpoint=None):
    """Tag `rows`; returns (output rows, metadata). Texts are tagged in batches, so a stopped job keeps a
    complete prefix of rows."""
    started = monotonic()
    texts = [text_of(row, columns) for row in rows]
    filled = [index for index, text in enumerate(texts) if text]
    log(f"{len(filled):,} dari {len(rows):,} baris berisi teks.".replace(",", "."))
    extra = [empty_values() for _ in rows]
    done, last = 0, monotonic()
    for start in range(0, len(filled), BATCH):
        if stop is not None and stop.is_set():
            break
        batch = filled[start:start + BATCH]
        for index, entities in zip(batch, tagger.tag([texts[i] for i in batch])):
            extra[index] = values_for(entities)
        done = start + len(batch)
        if monotonic() - last >= 15 or done == len(filled):
            log(f"Entitas bernama: {done:,} dari {len(filled):,} teks.".replace(",", "."))
            last = monotonic()
            if checkpoint:
                checkpoint(_output(rows, extra, filled[done - 1] + 1))
    stopped = stop is not None and stop.is_set()
    covered = filled[done - 1] + 1 if done else (0 if filled and stopped else len(rows))
    counts = Counter(item["jenis"] for values in extra[:covered] for item in parse(values[COLUMN])
                     if item["skor"] >= MIN_SCORE)
    metadata = {
        "model": MODEL_ID, "lisensi_model": MODEL_LICENSE, "catatan_data": DATA_NOTE,
        "revisi": getattr(tagger, "revision", ""), "perangkat": getattr(tagger, "device", ""),
        "token_maksimal": MAX_TOKENS, "tumpang_tindih": STRIDE, "batch": BATCH, "seed": SEED,
        "skor_minimal": MIN_SCORE, "jenis": list(TYPES), "kolom": list(columns), "baris": len(rows),
        "teks": done, "ringkasan": dict(counts), "detik": round(monotonic() - started, 1),
    }
    if stopped and done < len(filled):
        log("Dihentikan; baris yang sudah diproses disimpan.")
        return _output(rows, extra, covered), {**metadata, "dihentikan": True}
    log("Selesai.")
    return _output(rows, extra, len(rows)), metadata


def _output(rows, extra, count):
    return [{**row, **extra[index]} for index, row in enumerate(rows[:count])]


# --------------------------------------------------------------------------- #
# Summaries of a derived dataset
# --------------------------------------------------------------------------- #

def parse(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        items = json.loads(value)
    except ValueError:
        return []
    return [item for item in items if isinstance(item, dict) and "teks" in item and "jenis" in item]


def key_of(name):
    """Mentions that differ only in case or spacing count as one entity."""
    return " ".join(str(name).casefold().split())


def mentions(frame, kinds=None, min_score=MIN_SCORE):
    """One row per mention: document index, entity key, surface text, type."""
    if COLUMN not in frame:
        return pd.DataFrame(columns=["dokumen", "kunci", "teks", "jenis"])
    kinds = set(kinds or TYPES)
    records = [(index, key_of(item["teks"]), item["teks"], item["jenis"])
               for index, value in zip(frame.index, frame[COLUMN]) for item in parse(value)
               if item["jenis"] in kinds and float(item.get("skor", 1)) >= min_score]
    return pd.DataFrame(records, columns=["dokumen", "kunci", "teks", "jenis"])


def tagged_documents(frame):
    """Rows the job reached (an empty text still has '' in the column; rows past a stop are missing)."""
    if COLUMN not in frame:
        return 0
    return int(frame[COLUMN].astype(str).str.startswith("[").sum())


def _names(found):
    """Most frequent surface form and type of each entity key."""
    def most_common(column):
        counts = found.groupby(["kunci", column]).size().reset_index(name="n")
        counts = counts.sort_values(["kunci", "n", column], ascending=[True, False, True])
        return counts.drop_duplicates("kunci").set_index("kunci")[column]

    return most_common("teks"), most_common("jenis")


def entity_table(frame, kinds=None, min_score=MIN_SCORE, top=50):
    """Entities ranked by the number of documents that mention them."""
    found = mentions(frame, kinds, min_score)
    if found.empty:
        return pd.DataFrame()
    surface, kind = _names(found)
    documents = found.groupby("kunci")["dokumen"].nunique()
    total = max(tagged_documents(frame), 1)
    table = pd.DataFrame({"Entitas": surface, "Jenis": kind.map(TYPES), "Dokumen": documents,
                          "Sebutan": found.groupby("kunci").size()})
    table["Persen dokumen"] = (table["Dokumen"] / total * 100).round(1)
    return (table.sort_values(["Dokumen", "Sebutan"], ascending=False).head(top)
            .rename_axis("kunci").reset_index())


def type_table(frame, min_score=MIN_SCORE):
    """Mentions and distinct entities per type."""
    found = mentions(frame, None, min_score)
    if found.empty:
        return pd.DataFrame()
    table = found.groupby("jenis").agg(Sebutan=("kunci", "size"), Entitas=("kunci", "nunique"),
                                       Dokumen=("dokumen", "nunique"))
    table.index = table.index.map(TYPES)
    return table.sort_values("Sebutan", ascending=False).rename_axis("Jenis").reset_index()


def by_group(frame, group_column, kinds=None, min_score=MIN_SCORE, top=10):
    """Top entities of each group (portal): share of the group's documents that mention them."""
    found = mentions(frame, kinds, min_score)
    if found.empty or group_column not in frame:
        return pd.DataFrame()
    surface, _ = _names(found)
    groups = frame[group_column].astype(str).str.strip()
    found = found.assign(kelompok=groups.loc[found["dokumen"]].values)
    sizes = groups[frame[COLUMN].astype(str).str.startswith("[")].value_counts()
    counts = found.groupby(["kelompok", "kunci"])["dokumen"].nunique().reset_index(name="Dokumen")
    counts["Entitas"] = counts["kunci"].map(surface)
    counts["Persen dokumen"] = (counts["Dokumen"] / counts["kelompok"].map(sizes) * 100).round(1)
    counts = counts.sort_values(["kelompok", "Dokumen"], ascending=[True, False]).groupby("kelompok").head(top)
    return counts.rename(columns={"kelompok": "Kelompok"})[["Kelompok", "Entitas", "Dokumen", "Persen dokumen"]]


def over_time(frame, date_column, keys, period="ME", min_score=MIN_SCORE):
    """Documents per period that mention each entity in `keys`."""
    from workspace_app import analysis as an

    found = mentions(frame, None, min_score)
    if found.empty or date_column not in frame or not keys:
        return pd.DataFrame()
    surface, _ = _names(found)
    dates = an.parse_dates(frame[date_column])
    found = found[found["kunci"].isin(keys)]
    found = found.assign(periode=dates.loc[found["dokumen"]].values)
    found = found.dropna(subset=["periode"]).drop_duplicates(["dokumen", "kunci"])
    if found.empty:
        return pd.DataFrame()
    found["periode"] = pd.to_datetime(found["periode"]).dt.to_period(period.rstrip("E")).dt.to_timestamp()
    table = pd.crosstab(found["periode"], found["kunci"])
    return table.rename(columns=surface)[[surface[key] for key in keys if key in table]]


def cooccurrence(frame, kinds=None, min_score=MIN_SCORE, top=50, minimum=2):
    """Pairs of entities named in the same document: count and Jaccard index of their document sets."""
    found = mentions(frame, kinds, min_score)
    if found.empty:
        return pd.DataFrame()
    surface, _ = _names(found)
    per_document = found.groupby("dokumen")["kunci"].agg(lambda values: sorted(set(values)))
    pairs, singles = Counter(), Counter()
    for names in per_document:
        singles.update(names)
        pairs.update(combinations(names, 2))
    rows = [{"Entitas A": surface[a], "Entitas B": surface[b], "Dokumen bersama": count,
             "Jaccard": round(count / (singles[a] + singles[b] - count), 3)}
            for (a, b), count in pairs.items() if count >= minimum]
    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(rows).sort_values(["Dokumen bersama", "Jaccard"], ascending=False).head(top)
            .reset_index(drop=True))


def documents_with(frame, key, text_columns, top=100, min_score=MIN_SCORE):
    """The texts that mention one entity."""
    found = mentions(frame, None, min_score)
    indexes = list(dict.fromkeys(found.loc[found["kunci"] == key, "dokumen"]))[:top]
    if not indexes:
        return pd.DataFrame()
    rows = frame.loc[indexes]
    table = pd.DataFrame({"Teks": [text_of(row, text_columns)[:600] for row in rows.to_dict("records")]})
    for name in ("portal", "sumber_data"):
        if name in rows:
            table.insert(0, "Portal", rows[name].values)
            break
    return table


def edges(frame, kinds=None, min_score=MIN_SCORE, minimum=2):
    """Co-occurrence edge list (Source, Target, Weight) for Gephi."""
    table = cooccurrence(frame, kinds, min_score, top=100_000, minimum=minimum)
    if table.empty:
        return table
    return table.rename(columns={"Entitas A": "Source", "Entitas B": "Target", "Dokumen bersama": "Weight"})[
        ["Source", "Target", "Weight", "Jaccard"]]


def method_text(metadata, origin):
    columns = ", ".join(metadata.get("kolom") or [])
    score = str(metadata.get("skor_minimal", MIN_SCORE)).replace(".", ",")
    return (
        f"Entitas bernama pada {origin} (kolom {columns}, dibaca dari teks asli tanpa normalisasi agar huruf kapital "
        f"tetap menjadi petunjuk) dikenali dengan model {metadata.get('model', MODEL_ID)} (revisi "
        f"{str(metadata.get('revisi', ''))[:12]}; arsitektur ModernBERT yang dilatih awal pada teks bahasa Indonesia, "
        f"lisensi {metadata.get('lisensi_model', MODEL_LICENSE)}) yang di-fine-tune pada korpus NERGrit dengan 19 "
        f"label; {METRIC}. Korpus NERGrit tidak mencantumkan lisensi yang jelas. Teks yang lebih panjang dari "
        f"{metadata.get('token_maksimal', MAX_TOKENS)} token dibagi menjadi jendela yang tumpang tindih "
        f"{metadata.get('tumpang_tindih', STRIDE)} token. Label BIO tiap token digabung menjadi entitas, dan skor "
        f"entitas adalah rata-rata probabilitas tokennya; entitas dengan skor di bawah {score} diabaikan, begitu pula "
        f"entitas tanpa satu pun huruf kapital (kata umum yang keliru dikenali sebagai nama). Hanya jenis "
        f"yang menamai sesuatu yang dianalisis (orang, organisasi, organisasi politik, wilayah, lokasi, fasilitas, "
        f"peristiwa, produk, hukum, karya, agama, bahasa); angka, tanggal, dan jumlah uang diabaikan. Sebutan yang "
        f"hanya berbeda huruf besar-kecil atau spasi dihitung sebagai satu entitas, tetapi variasi nama (misalnya "
        f"nama lengkap dan nama panggilan) tidak digabung. Frekuensi dihitung sebagai jumlah dokumen yang menyebut "
        f"entitas; kemunculan bersama dihitung per dokumen dengan indeks Jaccard. Model dapat keliru pada nama yang "
        f"jarang, singkatan, dan jabatan yang menempel pada nama, sehingga daftar entitas teratas perlu diperiksa."
    )
